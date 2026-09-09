"""Experimental alternative to ai_weather_briefing.py: instead of narrating slide text
from the promt_unformat*.json zone statistics, this captions each plot image directly with
a vision pass (prompts/image_caption_prompt.md), then synthesizes those captions into the
same 10-slide narrative format (prompts/system_prompt_from_images.md).

Standalone comparison script — writes a digest text file only, does not touch the PPTX.
Once the output quality is validated, the PPTX-population block from ai_weather_briefing.py
(everything from "prs = Presentation(...)" onward) can be pointed at this script's `summary`
string unchanged, since both scripts produce the same ---SLIDE--- delimited format.
"""
from google import genai
from google.genai import types
import os
from datetime import datetime, timedelta

prefix = os.environ["MAIN_PATH"]

if "DATE_STR" in os.environ:
    date_str = os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

with open(f"{prefix}/prompts/image_caption_prompt.md") as f:
    caption_system_prompt = f.read()

with open(f"{prefix}/prompts/system_prompt_from_images.md") as f:
    synthesis_system_prompt = f.read()

kenya_path = f"plots/Kenya/{date_str}"

# Same plot_paths mapping as ai_weather_briefing.py (slide_types <-> plots), duplicated
# here per this repo's convention of not sharing such mappings across scripts.
slide_types = ["ECMWF_raw", "GEFS_raw", "ECMWF_dwn", "ECMWFmed_raw",
               "ECMWF_tercile", "ECMWF_efi", "ECMWF_p50", "ECMWF_p50anom"]
plots = ["weekly_precip", "gefs_weekly_precip", "weekly_precip_downscaled",
         "weekly_medium_range_precip", "chance_of_above_or_below", "efi_sot_precip",
         "50th_percentile_exedance", "anomaly_from_50th"]
plot_paths = {t: f"{prefix}/{kenya_path}/weekly/{p}.png" for t, p in zip(slide_types, plots)}

slide_labels = {
    "ECMWF_raw": "ECMWF S2S weekly precipitation forecast",
    "GEFS_raw": "GEFS weekly precipitation forecast",
    "ECMWF_dwn": "downscaled ECMWF precipitation forecast",
    "ECMWFmed_raw": "medium-range ECMWF precipitation forecast",
    "ECMWF_tercile": "tercile balance (above/below normal)",
    "ECMWF_efi": "extreme forecast index (EFI)",
    "ECMWF_p50": "probability of exceeding the median (p50 exceedance)",
    "ECMWF_p50anom": "ensemble mean anomaly (p50anom)",
}

client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
model_id = "gemini-3.1-flash-lite"

captions = {}
for t in slide_types:
    path = plot_paths[t]
    if not os.path.exists(path):
        print(f"WARNING: missing plot for '{t}': {path}, skipping caption")
        captions[t] = "No plot was available for this slide in this run."
        continue

    with open(path, "rb") as f:
        image_bytes = f.read()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")

    response = client.models.generate_content(
        model=model_id,
        contents=[
            image_part,
            f"Context: this is the pipeline's '{slide_labels[t]}' output for {date_str}.",
        ],
        config=types.GenerateContentConfig(
            system_instruction=caption_system_prompt,
            max_output_tokens=500,
        ),
    )
    captions[t] = response.text.strip()
    print(f"--- {t} ---\n{captions[t]}\n")

caption_block = "\n".join(f"{t} ({slide_labels[t]}): {captions[t]}" for t in slide_types)

user_prompt = f"""
Forecast date: {date_str}
Country: Kenya
Month: {date_str[5:7]}
Plot captions:
{caption_block}
"""

response = client.models.generate_content(
    model=model_id,
    contents=user_prompt,
    config=types.GenerateContentConfig(
        system_instruction=synthesis_system_prompt,
        max_output_tokens=6000,
    ),
)
summary = response.text

with open(f"{prefix}/prompts/digest_vision_{date_str}.txt", "w") as f:
    f.write(summary)

print(f"\nWrote synthesized digest to prompts/digest_vision_{date_str}.txt")
