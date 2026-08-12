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

types = ["date", 'ECMWF_raw', 'GEFS_raw', "ECMWF_dwn", "ECMWFmed_raw",
         "ECMWF_tercile", "ECMWF_efi", "ECMWF_p50", "ECMWF_p50anom", "sum","waves"]
plots = ['hold', 'weekly_precip', "gefs_weekly_precip", "weekly_precip_downscaled",
         "weekly_medium_range_precip", "chance_of_above_or_below", "efi_sot_precip",
         "50th_percentile_exedance", "anomaly_from_50th","summary"]
plots_path = [f"plots/Kenya/{date_str}/weekly/{i}.png" for i in plots]

wave_map_path = f"plots/Kenya/{date_str}/weekly/wave_map.png"
os.makedirs(os.path.dirname(wave_map_path), exist_ok=True)
wave_map_resp = requests.get("https://ncics.org/pub/mjo/v2/map/olr.cfs.all.global.7.png", timeout=30)
wave_map_resp.raise_for_status()
with open(wave_map_path, "wb") as f:
    f.write(wave_map_resp.content)

hov_moller_path = f"plots/Kenya/{date_str}/weekly/hov_moller.png"
os.makedirs(os.path.dirname(hov_moller_path), exist_ok=True)
hov_moller_resp = requests.get("https://ncics.org/pub/mjo/v2/hov/olr.cfs.eqtr.png", timeout=30)
hov_moller_resp.raise_for_status()
with open(hov_moller_path, "wb") as f:
    f.write(hov_moller_resp.content)

for i, t in enumerate(types):
    slide = prs.slides[i]
    for shape in slide.shapes:
        if shape.name == f"{t}_text":
            if t == 'date':
                dt_obj = datetime.fromisoformat(date_str)
                day = dt_obj.day
                suffix = ("th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th"))
                formatted_date = f"{dt_obj.strftime('%B')} {day}{suffix}, {dt_obj.strftime('%Y')}"
                set_slide_text(shape, formatted_date, font_size=35, font_name='Karla Medium',align=PP_ALIGN.CENTER)
            elif t =='sum':
                set_slide_text(shape, slide_text[i], font_size=17)
            else:
                set_slide_text(shape, slide_text[i], font_size=13)
        elif shape.name == f"{t}_plot":
            left, top, width, height = shape.left, shape.top, shape.width, shape.height
            shape._element.getparent().remove(shape._element)
            slide.shapes.add_picture(plots_path[i], left, top, width, height)
        elif shape.name == "wave_map":
            left, top, width, height = shape.left, shape.top, shape.width, shape.height
            shape._element.getparent().remove(shape._element)
            slide.shapes.add_picture(wave_map_path, left, top, width, height)
        elif shape.name == "hov_moller":
            left, top, width, height = shape.left, shape.top, shape.width, shape.height
            shape._element.getparent().remove(shape._element)
            slide.shapes.add_picture(hov_moller_path, left, top, width, height)


prs.save(f"s2s_briefing_{date_str}.pptx")