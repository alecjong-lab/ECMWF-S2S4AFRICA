"""Experimental alternative to ai_weather_briefing.py: instead of narrating slide text
from the promt_unformat*.json zone statistics, this captions each plot image directly with
a vision pass (prompts/image_caption_prompt.md), then synthesizes those captions into the
same 10-slide narrative format (prompts/system_prompt_from_images.md).

Standalone comparison script — writes a digest text file only, does not touch the PPTX.
Once the output quality is validated, the PPTX-population block from ai_weather_briefing.py
(everything from "prs = Presentation(...)" onward) can be pointed at this script's `summary`
string unchanged, since both scripts produce the same ---SLIDE--- delimited format.

Captions are written by Claude Sonnet through the claude CLI by default (see CAPTION_BACKEND
below; in CI it logs in with the CLAUDE_CODE_OAUTH_TOKEN secret) and also saved on their own to
prompts/captions_vision_<date>.txt. prompts/plot_descriptions/caption_eval.py is where the caption
prompts and models were compared.
"""
from google import genai
from google.genai import types
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta

prefix = os.environ["MAIN_PATH"]

# Briefing date is today. Original ECMWF plots live under the lagged init.
ECMWF_LAG_DAYS = 2
date_str = os.environ.get("DATE_STR") or datetime.today().strftime("%Y-%m-%d")
if "ECMWF_DATE_STR" in os.environ:
    ecmwf_date_str = os.environ["ECMWF_DATE_STR"]
elif os.path.isdir(f"{prefix}/plots/Kenya/{date_str}"):
    ecmwf_date_str = date_str
else:
    ecmwf_date_str = (
        datetime.fromisoformat(date_str) - timedelta(days=ECMWF_LAG_DAYS)
    ).strftime("%Y-%m-%d")

# The downscaled forecast and the 0.4 degree plots are dated by the 0.4 degree init,
# normally a day newer (see ai_weather_briefing.py and forecast_04deg_dates.py)
downscaled_date_str = os.environ.get("DOWNSCALED_DATE_STR") or ecmwf_date_str
forecast_04deg_date_str = os.environ.get("FORECAST_04DEG_DATE_STR") or ecmwf_date_str

with open(f"{prefix}/prompts/image_caption_prompt.md") as f:
    caption_system_prompt = f.read()

with open(f"{prefix}/prompts/system_prompt_from_images.md") as f:
    synthesis_system_prompt = f.read()

kenya_path = f"plots/Kenya/{ecmwf_date_str}"

# Same plot_paths mapping as ai_weather_briefing.py (slide_types <-> plots), duplicated
# here per this repo's convention of not sharing such mappings across scripts.
slide_types = ["ECMWF_raw", "GEFS_raw", "ECMWF_dwn", "ECMWFmed_raw",
               "ECMWF_tercile", "ECMWF_efi", "ECMWF_p50", "ECMWF_p50anom"]
plots = ["weekly_precip", "gefs_weekly_precip", "weekly_precip_downscaled",
         "weekly_medium_range_precip", "chance_of_above_or_below", "efi_sot_precip",
         "50th_percentile_exedance", "anomaly_from_50th"]
plot_paths = {t: f"{prefix}/{kenya_path}/weekly/{p}.png" for t, p in zip(slide_types, plots)}
plot_dates = {t: ecmwf_date_str for t in slide_types}
plot_paths["ECMWF_dwn"] = f"{prefix}/plots/Kenya/{downscaled_date_str}/weekly/weekly_precip_downscaled.png"
plot_dates["ECMWF_dwn"] = downscaled_date_str
# the briefing shows the 0.4 degree versions of these when they're there (prefer_04deg in
# ai_weather_briefing.py), so they are the ones captioned
for t in ("ECMWF_tercile", "ECMWF_efi", "ECMWF_p50", "ECMWF_p50anom"):
    alt = f"{prefix}/plots/Kenya/{forecast_04deg_date_str}/04deg/weekly/{os.path.basename(plot_paths[t])}"
    if os.path.exists(alt):
        plot_paths[t], plot_dates[t] = alt, forecast_04deg_date_str

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

# How to read each plot, added to the compact caption prompt. Same text as PLOT_GUIDE in
# prompts/plot_descriptions/caption_eval.py, where the prompt was tested.
plot_guide = {
    "weekly_precip": "Weekly ensemble-mean total precipitation from ECMWF S2S on a coarse 1.5 degree grid, one panel per week. The colorbar maximum changes from day to day, so read the numbers off this colorbar.",
    "gefs_weekly_precip": "Weekly ensemble-mean total precipitation from NOAA GEFS, one panel per week (often 5 weeks). The colorbar maximum changes from day to day. Heavy rain at the top edge of the map is usually in southern Ethiopia, not Kenya.",
    "weekly_precip_downscaled": "Weekly total precipitation from ECMWF downscaled to a fine grid, one panel per week. Point out where the maxima are (often the west near Lake Victoria, the central highlands just south of the equator, and the coast) and where it stays dry (usually the northwest).",
    "weekly_medium_range_precip": "Weekly total precipitation from the ECMWF medium-range ensemble, only two panels (week 1 and week 2). The colorbar maximum changes from day to day, so read the numbers off this colorbar.",
    "chance_of_above_or_below": "Tercile balance: P(above normal) minus P(below normal), in percentage points from -100 to +100. Teal/green = above normal favoured, brown = below normal favoured, white = no lean. Report it as points (for example +75 to +100 points), never as a probability.",
    "efi_sot_precip": "Extreme Forecast Index for weekly precipitation, shaded only from 0.5 (white) to 1.0 (red), with black Shift of Tails contours. Unshaded panels mean no wet-extreme signal. Say where and in which weeks the EFI is shaded, with its value range, and whether SOT contours sit on it. Many forecasts have little or no signal; then say so briefly.",
    "50th_percentile_exedance": "Probability that the weekly total exceeds the model-climate median, in fixed bins 0/1/10/25/45/55/75/90/99/100%. Purple and blues are low (drier than normal), light green is 45 to 55% (normal odds), yellow to dark red are high (wetter than normal). Quote the bins, for example 90 to 99%.",
    "anomaly_from_50th": "Ensemble-mean weekly precipitation minus the model-climate median, in mm (kg m**-2). Blue = wetter than the median, red = drier (the colormap is RdBu). The colorbar range changes from day to day, so read the numbers off this colorbar.",
}

# CAPTION_PROMPT=compact (default) uses prompts/image_caption_prompt_compact.md plus the plot
# guide and three Opus-written reference captions of the same plot from
# prompts/plot_descriptions/descriptions.jsonl; "full" uses prompts/image_caption_prompt.md alone.
# CAPTION_BACKEND=claude (default) captions with the claude CLI (CAPTION_MODEL, default sonnet,
# logged in through CLAUDE_CODE_OAUTH_TOKEN) and falls back to Gemini per image if that fails;
# CAPTION_BACKEND=gemini uses Gemini only. The synthesis step below always uses Gemini.
caption_prompt = os.environ.get("CAPTION_PROMPT", "compact")
caption_backend = os.environ.get("CAPTION_BACKEND", "claude")
claude_model = os.environ.get("CAPTION_MODEL", "sonnet")
model_id = "gemini-3.1-flash-lite"
client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

if caption_prompt == "compact":
    with open(f"{prefix}/prompts/image_caption_prompt_compact.md") as f:
        caption_system_prompt = f.read()
    reference_captions = []
    descriptions = f"{prefix}/prompts/plot_descriptions/descriptions.jsonl"
    if os.path.exists(descriptions):
        with open(descriptions, encoding="utf-8") as f:
            reference_captions = [json.loads(line) for line in f]


def caption_request(plot):
    """User-turn text for one plot: context, plus the guide and examples for the compact prompt."""
    t = plot_slide[plot]
    text = f"Context: this is the pipeline's '{slide_labels[t]}' output for {plot_dates[t]}."
    if "/04deg/" in plot_paths[t]:
        text += " It is made from the 0.4 degree ECMWF forecast; take the number of weeks from its panels."
    if caption_prompt != "compact":
        return text
    text += f"\nHow to read this plot: {plot_guide[plot]}"
    pool = sorted((r for r in reference_captions if r["plot"] == plot and r["date"] != plot_dates[t]),
                  key=lambda r: r["date"])
    examples = [pool[int(i * len(pool) / 3)] for i in range(3)] if len(pool) > 3 else pool
    if examples:
        shots = "\n\n".join(f"Example ({e['date']}):\n{e['description']}" for e in examples)
        text += ("\n\nReference captions of this plot type from other dates, showing the expected style, "
                 f"length and level of detail. Their numbers and places do not apply to this image:\n\n{shots}")
    return text


def caption_with_claude(path, request):
    """Caption one image with the claude CLI, which opens the image with its Read tool."""
    prompt = (f"{request}\n\nRead the image file {os.path.abspath(path)} with the Read tool, then write "
              "the caption. Output only the caption.")
    with tempfile.TemporaryDirectory() as cwd:  # outside the repo, so its CLAUDE.md is not loaded
        system_file = os.path.join(cwd, "system.md")
        with open(system_file, "w", encoding="utf-8") as f:
            f.write(caption_system_prompt)
        out = subprocess.run(
            [shutil.which("claude") or "claude", "-p", "--model", claude_model,
             "--system-prompt-file", system_file, "--tools", "Read", "--allowedTools", "Read",
             "--add-dir", os.path.dirname(os.path.abspath(path)),
             "--output-format", "json", "--no-session-persistence"],
            input=prompt, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=300)
    if not out.stdout.strip():
        raise RuntimeError(out.stderr.strip()[-500:])
    result = json.loads(out.stdout)
    if result.get("is_error"):
        raise RuntimeError(result.get("result"))
    return result["result"].strip()


def caption_with_gemini(path, request):
    with open(path, "rb") as f:
        image_part = types.Part.from_bytes(data=f.read(), mime_type="image/png")
    response = client.models.generate_content(
        model=model_id,
        contents=[image_part, request],
        config=types.GenerateContentConfig(
            system_instruction=caption_system_prompt,
            max_output_tokens=2000,
        ),
    )
    return response.text.strip()


plot_slide = dict(zip(plots, slide_types))
captions, caption_models = {}, {}
for t, plot in zip(slide_types, plots):
    path = plot_paths[t]
    if not os.path.exists(path):
        print(f"WARNING: missing plot for '{t}': {path}, skipping caption")
        captions[t] = "No plot was available for this slide in this run."
        continue

    request = caption_request(plot)
    if caption_backend == "claude":
        try:
            captions[t], caption_models[t] = caption_with_claude(path, request), f"claude {claude_model}"
        except Exception as e:
            print(f"WARNING: claude caption failed for '{t}' ({e}), falling back to {model_id}")
    if t not in captions:
        captions[t], caption_models[t] = caption_with_gemini(path, request), model_id
    print(f"--- {t} ({caption_models[t]}) ---\n{captions[t]}\n")

# The captions on their own, for checking them against the plots.
with open(f"{prefix}/prompts/captions_vision_{date_str}.txt", "w", encoding="utf-8") as f:
    for t, plot in zip(slide_types, plots):
        f.write(f"{plot} ({caption_models.get(t, 'no plot')}):\n{captions[t]}\n\n")

caption_block = "\n".join(f"{t} ({slide_labels[t]}): {captions[t]}" for t in slide_types)

_ecmwf_lag_note = ""
if ecmwf_date_str != date_str:
    _ecmwf_lag_note = (
        f"ECMWF / GEFS products are from init {ecmwf_date_str} "
        f"({ECMWF_LAG_DAYS}-day publication lag).\n"
    )
if downscaled_date_str != ecmwf_date_str or forecast_04deg_date_str != ecmwf_date_str:
    _ecmwf_lag_note += (
        f"The downscaled forecast is from init {downscaled_date_str} and the 0.4 degree ECMWF "
        f"plots from init {forecast_04deg_date_str}.\n"
    )
user_prompt = f"""
Forecast date: {date_str}
{_ecmwf_lag_note}Country: Kenya
Month: {date_str[5:7]}
Plot captions:
{caption_block}
"""

response = client.models.generate_content(
    model=model_id,
    contents=user_prompt,
    config=types.GenerateContentConfig(
        system_instruction=synthesis_system_prompt,
        max_output_tokens=2500,
    ),
)
summary = response.text

with open(f"{prefix}/prompts/digest_vision_{date_str}.txt", "w") as f:
    f.write(summary)

print(f"\nWrote synthesized digest to prompts/digest_vision_{date_str}.txt")
