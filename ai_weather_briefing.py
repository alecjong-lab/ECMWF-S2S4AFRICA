from google import genai
from google.genai import types
import os
import get_ECMWF_functions as gef
from datetime import datetime, timedelta
from pptx import Presentation
from pptx.util import Pt
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
import requests

prefix=os.environ["MAIN_PATH"]

if "DATE_STR" in os.environ:
    date_str=os.environ["DATE_STR"]
else:
    today = datetime.today()
    two_days_earlier = today - timedelta(days=2)
    date_str = two_days_earlier.strftime("%Y-%m-%d")

with open(f"{prefix}/prompts/system_prompt.md") as f:
            system_prompt = f.read()

def _load_or_empty(path):
    try:
        return gef.load_dict(path)
    except FileNotFoundError:
        return {}

promt_unformat1=_load_or_empty(f"{prefix}/promt_unformat1.json")
promt_unformat2=_load_or_empty(f"{prefix}/promt_unformat2.json")
promt_unformat3=_load_or_empty(f"{prefix}/promt_unformat3.json")

promt_unformat= promt_unformat1 | promt_unformat2 | promt_unformat3
user_prompt = f"""
Forecast date: {date_str}
Country: Kenya
Month: {date_str[5:7]}
Zone statistics (6-week forecast):
{gef.format_prompt_data(promt_unformat)}
"""

client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

response = client.models.generate_content(
    model="gemini-3.1-flash-lite",
    contents=user_prompt,
    config=types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=6000,
    )
)
summary = response.text

# var_ex='''\n \nLegend:\np33= Percentage of ensemble members below normal of model climate
# p66= Percentage of ensemble members above normal of model climate
# p50anom= Anomaly of the ensemble mean from the median of the model climate in % and mm
# efi= Extreme forecast index'''

# with open(f'{prefix}/prompts/digest_{date_str}.txt', 'w') as f:
#     f.write(summary)

def set_slide_text(shape, text, font_size=12, font_name="Calibri", align=None):
    tf = shape.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE

    paragraphs = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    for idx, para_text in enumerate(paragraphs):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()

        if align is not None:
            p.alignment = align

        pPr = p._p.get_or_add_pPr()
        for tag in ("a:buChar", "a:buAutoNum", "a:buNone"):
            el = pPr.find(qn(tag))
            if el is not None:
                pPr.remove(el)
        pPr.append(pPr.makeelement(qn("a:buNone"), {}))
        pPr.set("marL", "0")
        pPr.set("indent", "0")

        p.space_after = Pt(8)

        colon_idx = para_text.find(":")
        if 4 < colon_idx < 250:
            header = para_text[:colon_idx + 1]
            rest = para_text[colon_idx + 1:]

            bold_run = p.add_run()
            bold_run.text = header
            bold_run.font.bold = True
            bold_run.font.size = Pt(font_size)
            bold_run.font.name = font_name

            rest_run = p.add_run()
            rest_run.text = rest
            rest_run.font.bold = False
            rest_run.font.size = Pt(font_size)
            rest_run.font.name = font_name
        else:
            run = p.add_run()
            run.text = para_text
            run.font.size = Pt(font_size)
            run.font.name = font_name

prs = Presentation("WeatherbriefingKenya_template2.pptx")
text = summary
slide_text = text.split("---SLIDE---")

slide_types = ["date", 'ECMWF_raw', 'GEFS_raw', "ECMWF_dwn", "ECMWFmed_raw",
               "ECMWF_tercile", "ECMWF_efi", "ECMWF_p50", "ECMWF_p50anom", "sum", "waves", "IOD"]
plots = ['hold', 'weekly_precip', "gefs_weekly_precip", "weekly_precip_downscaled",
         "weekly_medium_range_precip", "chance_of_above_or_below", "efi_sot_precip",
         "50th_percentile_exedance", "anomaly_from_50th", "summary"]

kenya_path = f"plots/Kenya/{date_str}"
diagnostics_path = f"plots/diagnostics/{date_str}/monthly"

# only the first len(plots) slide types have a "{type}_plot" shape
plot_paths = {t: f"{kenya_path}/weekly/{p}.png" for t, p in zip(slide_types, plots)}

def download_to(url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)

wave_map_path = f"{kenya_path}/weekly/wave_map.png"
download_to("https://ncics.org/pub/mjo/v2/map/olr.cfs.all.global.7.png", wave_map_path)

hov_moller_path = f"{kenya_path}/weekly/hov_moller.png"
download_to("https://ncics.org/pub/mjo/v2/hov/olr.cfs.eqtr.png", hov_moller_path)

# Indian Ocean moisture diagnostics (see IndianOceanState.py)
IOD_path = f"{diagnostics_path}/ECMWF_s2s_10wind_sst_anomaly_{date_str}.png"
IO_ivt_path = f"{diagnostics_path}/ECMWF_s2s_ivt_u_{date_str}.png"
IO_TCWV_anom_path = f"{diagnostics_path}/ECMWF_s2s_tcw_anomaly_{date_str}.png"
IO_precip_anom_path = f"{diagnostics_path}/ECMWF_s2s_precip_anomaly_{date_str}.png"
IO_precip_anom_std_path = f"{diagnostics_path}/ECMWF_s2s_precip_std_anomaly_{date_str}.png"

# rainy season onset maps (see run_rainfall_onset.py) -- wet-spell/no-dry-spell
# definition, and the two-stage cumulative-rainfall ("accum") definition
onsetecmwf_path = f"{kenya_path}/monthly/onset_s2s.png"
onsetgefs_path = f"{kenya_path}/monthly/onset_gefs.png"
onsetecmwf_accum_path = f"{kenya_path}/monthly/onset_s2s_accum.png"
onsetgefs_accum_path = f"{kenya_path}/monthly/onset_gefs_accum.png"

# dry/wet spell probability & median length maps (see plot_s2s.py)
median_wet_path = f"{kenya_path}/monthly/median_wetspell_length.png"
wet5_path = f"{kenya_path}/monthly/prob_wetspell_5days.png"
wet7_path = f"{kenya_path}/monthly/prob_wetspell_7days.png"
median_dry_path = f"{kenya_path}/monthly/median_dryspell_length.png"
dry5_path = f"{kenya_path}/monthly/prob_dryspell_5days.png"
dry7_path = f"{kenya_path}/monthly/prob_dryspell_7days.png"

exceed20mm_path = f"{kenya_path}/weekly/chance_higherthan_20mm.png"

picture_paths = {
    "wave_map": wave_map_path,
    "hov_meuller": hov_moller_path,
    "IO_state": IOD_path,
    "IO_ivt": IO_ivt_path,
    "IO_TCWV_anom": IO_TCWV_anom_path,
    "IO_precip_anom": IO_precip_anom_path,
    "IO_precip_anom_std": IO_precip_anom_std_path,
    "Onset_ECMWF": onsetecmwf_path,
    "Onset_GEFS": onsetgefs_path,
    "Onset_ECMWF_accum": onsetecmwf_accum_path,
    "Onset_GEFS_accum": onsetgefs_accum_path,
    "median_wet": median_wet_path,
    "wet5": wet5_path,
    "wet7": wet7_path,
    "median_dry": median_dry_path,
    "dry5": dry5_path,
    "dry7": dry7_path,
    "exceed20mm": exceed20mm_path,
}

def replace_picture(slide, shape, image_path):
    left, top, width, height = shape.left, shape.top, shape.width, shape.height
    shape._element.getparent().remove(shape._element)
    slide.shapes.add_picture(image_path, left, top, width, height)

for i, slide in enumerate(prs.slides):
    t = slide_types[i] if i < len(slide_types) else None
    for shape in slide.shapes:
        if t is not None and shape.name == f"{t}_text":
            if t == 'date':
                dt_obj = datetime.fromisoformat(date_str)
                day = dt_obj.day
                suffix = ("th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th"))
                formatted_date = f"{dt_obj.strftime('%B')} {day}{suffix}, {dt_obj.strftime('%Y')}"
                set_slide_text(shape, formatted_date, font_size=35, font_name='Karla Medium', align=PP_ALIGN.CENTER)
            elif t == 'sum':
                set_slide_text(shape, slide_text[i], font_size=17)
            else:
                set_slide_text(shape, slide_text[i], font_size=13)
        elif t is not None and shape.name == f"{t}_plot":
            replace_picture(slide, shape, plot_paths[t])
        elif shape.name in picture_paths:
            replace_picture(slide, shape, picture_paths[shape.name])

prs.save(f"s2s_briefing_{date_str}.pptx")