from google import genai
from google.genai import types
import os
import sys
import get_ECMWF_functions as gef
from datetime import datetime, timedelta
from pptx import Presentation
from pptx.util import Pt
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
import json
import shutil
import subprocess
import requests

prefix=os.environ["MAIN_PATH"]

# Google Slides template (weather_briefing_kenya_template). Downloaded via the
# rclone gdrive remote (same RCLONE_CONFIG as s2s-emails.xlsx). The file can
# stay private if that Google account has access. Override with
# BRIEFING_TEMPLATE_ID, GOOGLE_DRIVE_TOKEN, or BRIEFING_TEMPLATE_PATH.
TEMPLATE_SLIDES_ID = os.environ.get(
    "BRIEFING_TEMPLATE_ID", "1zSp3C35PqDfMKbT8WtEcxoG2EoyIAJA5"
)
TEMPLATE_EXPORT_URL = (
    f"https://docs.google.com/presentation/d/{TEMPLATE_SLIDES_ID}/export/pptx"
)
DRIVE_EXPORT_URL = (
    f"https://www.googleapis.com/drive/v3/files/{TEMPLATE_SLIDES_ID}/export"
    "?mimeType=application/vnd.openxmlformats-officedocument.presentationml.presentation"
    "&supportsAllDrives=true"
)

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

try:
    promt_unformat1 = gef.add_onset_from_netcdf(
        promt_unformat1, f"{prefix}/data/{date_str}/rainfall_onset_s2s_Kenya.nc"
    )
    gef.save_dict(promt_unformat1, f"{prefix}/promt_unformat1.json")
except Exception as exc:
    print(f"add_onset_from_netcdf failed: {exc}")

promt_unformat= promt_unformat1 | promt_unformat2 | promt_unformat3
user_prompt = f"""
Forecast date: {date_str}
Country: Kenya
Month: {date_str[5:7]}
Zone statistics (6-week forecast).
Onset dates come from the rainfall-onset action (first 3-day spell of at least 20 mm with no 7 consecutive days below 1 mm in the next 21 days), median over ensemble members and grid cells in each region. Use them in the Indicators paragraph of the Overall Summary for **OND Short Rains** onset (typical mid-October). Never describe MAM Long Rains onset. Do not infer onset from weekly totals.
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

def _rclone_drive_token(remote="gdrive"):
    subprocess.run(
        ["rclone", "about", f"{remote}:"],
        check=False,
        capture_output=True,
        text=True,
    )
    dump = subprocess.run(
        ["rclone", "config", "dump"],
        check=True,
        capture_output=True,
        text=True,
    )
    remotes = json.loads(dump.stdout)
    token_raw = remotes[remote]["token"]
    token = json.loads(token_raw) if isinstance(token_raw, str) else token_raw
    access = token.get("access_token")
    if not access:
        raise RuntimeError("rclone gdrive remote has no access_token")
    return access


def _drive_access_token():
    env_token = os.environ.get("GOOGLE_DRIVE_TOKEN")
    if env_token:
        return env_token
    if shutil.which("rclone"):
        return _rclone_drive_token(os.environ.get("BRIEFING_RCLONE_REMOTE", "gdrive"))
    return None


def _write_pptx(dest_path, content, source):
    if not content.startswith(b"PK"):
        raise RuntimeError(
            f"Drive template export did not return a pptx (source={source})"
        )
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(content)
    return dest_path


def download_slides_template(dest_path):
    token = None
    try:
        token = _drive_access_token()
    except Exception as exc:
        print(f"WARNING: could not get a Drive token ({exc})", file=sys.stderr)

    if token:
        resp = requests.get(
            DRIVE_EXPORT_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=120,
        )
        resp.raise_for_status()
        return _write_pptx(dest_path, resp.content, "Drive API")

    print(
        "WARNING: no Drive credentials; falling back to the public Slides export URL",
        file=sys.stderr,
    )
    resp = requests.get(TEMPLATE_EXPORT_URL, timeout=120)
    resp.raise_for_status()
    return _write_pptx(dest_path, resp.content, TEMPLATE_EXPORT_URL)


def shape_keys(shape):
    """PowerPoint name plus Google Slides alt-text title/description.

    A Slides → pptx export typically renames objects to 'Google Shape;…';
    placeholder ids are preserved as cNvPr @title / @descr.
    """
    keys = []
    seen = set()

    def _add(value):
        if not value:
            return
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            keys.append(value)

    _add(getattr(shape, "name", None))
    for el in shape._element.iter():
        if el.tag.endswith("}cNvPr"):
            _add(el.get("title"))
            _add(el.get("descr"))
            break
    return keys


def first_mapped(shape, mapping):
    for key in shape_keys(shape):
        if key in mapping:
            return key, mapping[key]
    return None, None


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

template_path = os.environ.get("BRIEFING_TEMPLATE_PATH")
if not template_path:
    template_path = os.path.join(prefix, "WeatherbriefingKenya_template_download.pptx")
    try:
        download_slides_template(template_path)
    except Exception as exc:
        fallback = "WeatherbriefingKenya_template3.pptx"
        if os.path.isfile(fallback):
            print(
                f"WARNING: Drive template download failed ({exc}); using {fallback}",
                file=sys.stderr,
            )
            template_path = fallback
        else:
            raise

prs = Presentation(template_path)
text = summary
slide_text = text.split("---SLIDE---")

slide_types = ["date", 'ECMWF_raw', 'GEFS_raw', "ECMWF_dwn", "ECMWFmed_raw",
               "ECMWF_tercile", "ECMWF_efi", "ECMWF_p50", "ECMWF_p50anom", "sum", "waves", "IOD"]
plots = ['hold', 'weekly_precip', "gefs_weekly_precip", "weekly_precip_downscaled",
         "weekly_medium_range_precip", "chance_of_above_or_below", "efi_sot_precip",
         "50th_percentile_exedance", "anomaly_from_50th", "summary"]

kenya_path = f"plots/Kenya/{date_str}"
great_horn_path = f"plots/Great_Horn/{date_str}"
diagnostics_path = f"plots/diagnostics/{date_str}/weekly"
diagnostics_monthly_path = f"plots/diagnostics/{date_str}/monthly"
briefing_plots_path = f"plots/briefing/{date_str}/"

# only the first len(plots) slide types have a "{type}_plot" shape
plot_paths = {t: f"{kenya_path}/weekly/{p}.png" for t, p in zip(slide_types, plots)}

# Indian Ocean moisture diagnostics (see IndianOceanState.py)
IOD_path = f"{diagnostics_path}/ECMWF_s2s_10wind_sst_anomaly_{date_str}.png"
IO_ivt_weekly_path = f"{diagnostics_path}/ECMWF_s2s_ivt_u_{date_str}.png"
IO_ivt_monthly_path = f"{diagnostics_monthly_path}/ECMWF_s2s_ivt_u_{date_str}.png"
IO_TCWV_anom_path = f"{diagnostics_path}/ECMWF_s2s_tcw_anomaly_{date_str}.png"
IO_precip_anom_path = f"{diagnostics_path}/ECMWF_s2s_precip_anomaly_{date_str}.png"
IO_precip_anom_std_path = f"{diagnostics_path}/ECMWF_s2s_precip_std_anomaly_{date_str}.png"

# rainy season onset maps (see run_rainfall_onset.py) -- wet-spell/no-dry-spell
# definition, and the two-stage cumulative-rainfall ("accum") definition
onsetecmwf_path = f"{kenya_path}/monthly/onset_s2s.png"
onsetgefs_path = f"{kenya_path}/monthly/onset_gefs.png"
onsetecmwf_accum_path = f"{kenya_path}/monthly/onset_s2s_accum.png"
onsetgefs_accum_path = f"{kenya_path}/monthly/onset_gefs_accum.png"

# ICPAC_10mm variant of the two plain onset maps above (10mm, not 20mm, wet-spell
# total) -- no 10mm variant of the accum definition. Generator stems are
# icpac10mm, not _10mm.
onsetecmwf_10mm_path = f"{kenya_path}/monthly/onset_s2s_icpac10mm.png"
onsetgefs_10mm_path = f"{kenya_path}/monthly/onset_gefs_icpac10mm.png"

# reforecast-archive climatology counterparts of the ECMWF onset maps above
# (see the "S2S reforecast climatology" block in run_rainfall_onset.py)
onsetecmwf_climatology_path = f"{kenya_path}/monthly/onset_s2s_climatology.png"
onsetecmwf_accum_climatology_path = f"{kenya_path}/monthly/onset_s2s_climatology_accum.png"
onsetecmwf_climatology_10mm_path = f"{kenya_path}/monthly/onset_s2s_climatology_icpac10mm.png"

# dry/wet spell probability & median length maps (see plot_s2s.py)
median_wet_path = f"{kenya_path}/monthly/median_wetspell_length.png"
wet5_path = f"{kenya_path}/monthly/prob_wetspell_5days.png"
wet7_path = f"{kenya_path}/monthly/prob_wetspell_7days.png"
median_dry_path = f"{kenya_path}/monthly/median_dryspell_length.png"
dry5_path = f"{kenya_path}/monthly/prob_dryspell_5days.png"
dry7_path = f"{kenya_path}/monthly/prob_dryspell_7days.png"

# reforecast-archive climatology counterparts of the wet-spell maps above
# (Kenya-only "climatological wet spell" block in plot_s2s.py; no dry-spell
# climatology has been computed yet)
median_wet_climatology_path = f"{kenya_path}/monthly/climatology_median_wetspell_length.png"
wet5_climatology_path = f"{kenya_path}/monthly/climatology_prob_wetspell_5days.png"
wet7_climatology_path = f"{kenya_path}/monthly/climatology_prob_wetspell_7days.png"

exceed20mm_path = f"{kenya_path}/weekly/chance_higherthan_20mm.png"

# Great Horn tercile plot (regional counterpart to the Kenya-only ECMWF_tercile_plot)
ecmwf_tercile_ea_path = f"{great_horn_path}/weekly/chance_of_above_or_below.png"

picture_paths = {
    "ECMWF_tercile_plot_EA": ecmwf_tercile_ea_path,
    "IO_state": IOD_path,
    "IO_ivt_weekly": IO_ivt_weekly_path,
    "IO_ivt_monthly": IO_ivt_monthly_path,
    "IO_TCWV_anom": IO_TCWV_anom_path,
    "IO_precip_anom": IO_precip_anom_path,
    "IO_precip_anom_std": IO_precip_anom_std_path,
    "Onset_ECMWF": onsetecmwf_path,
    "Onset_GEFS": onsetgefs_path,
    "Onset_ECMWF_accum": onsetecmwf_accum_path,
    "Onset_GEFS_accum": onsetgefs_accum_path,
    "Onset_ECMWF_10mm": onsetecmwf_10mm_path,
    "Onset_GEFS_10mm": onsetgefs_10mm_path,
    "Onset_ECMWF_climatology": onsetecmwf_climatology_path,
    "Onset_ECMWF_accum_climatology": onsetecmwf_accum_climatology_path,
    "Onset_ECMWF_climatology_10mm": onsetecmwf_climatology_10mm_path,
    "median_wet": median_wet_path,
    "wet5": wet5_path,
    "wet7": wet7_path,
    "median_dry": median_dry_path,
    "dry5": dry5_path,
    "dry7": dry7_path,
    "median_wet_climatology": median_wet_climatology_path,
    "wet5_climatology": wet5_climatology_path,
    "wet7_climatology": wet7_climatology_path,
    "exceed20mm": exceed20mm_path,
}

# Plots generated by ws_scripts/slide*.sh and collected into briefing_plots_path
# (see populate_briefing_template3.py) — picture-only, no AI narration.
briefing_plot_names = [
    "chirps_kenya_weekly_rainfall",
    "chirps_kenya_weekly_anomaly",
    "chirps_east_africa_weekly_rainfall",
    "chirps_east_africa_weekly_anomaly",
    "tahmo_kenya_cities_rainfall",
    "tahmo_kenya_cities_temperature",
    "tahmo_kenya_cities_humidity",
    "kenya_ond_weekly_rainfall_vs_climatology",
    "kenya_ond_weekly_standardized_anomaly",
    "kenya_weekly_rainfall_analog_years",
    "mjo_rmm_gefs",
    "mjo_rmm_ecmwf",
    "iod_observed",
    "enso_observed",
    "iod_forecast",
    "enso_forecast",
    "itcz_africa_latest",
    "hovmoller_olr_tropics",
    "olr_map_africa",
    "kenya_gefs_chirps_verify_5mm",
    "kenya_gefs_chirps_bias",
    "kenya_gefs_chirps_mae",
]
for name in briefing_plot_names:
    picture_paths[name] = f"{briefing_plots_path}/{name}.png"

def resolve_picture_path(path):
    """Prefer the given path; also try the icpac10mm <-> 10mm filename alias."""
    if os.path.exists(path):
        return path
    aliases = (
        ("_icpac10mm.png", "_10mm.png"),
        ("_10mm.png", "_icpac10mm.png"),
    )
    for old, new in aliases:
        if path.endswith(old):
            alt = path[: -len(old)] + new
            if os.path.exists(alt):
                return alt
    return path


def replace_picture(slide, shape, image_path):
    left, top, width, height = shape.left, shape.top, shape.width, shape.height
    shape._element.getparent().remove(shape._element)
    slide.shapes.add_picture(image_path, left, top, width, height)

# Missing pictures that should block the whole send (rather than going out with a
# stale template placeholder in that slide) are tracked here and checked just
# before prs.save() below.
required_missing = []

# ws_scripts/slide*.sh-sourced pictures are still being wired into this pipeline
# (see populate_briefing_template3.py) and aren't reliably populated every day yet,
# so a missing one is tolerated (warn, keep the template placeholder) rather than
# blocking the send like the core forecast/diagnostic pictures below.
optional_picture_names = set(briefing_plot_names)

# Reforecast-archive climatology plots are new and depend on an extra network
# fetch (Planette's reforecast archive) on top of the core pipeline, so a
# missing one is tolerated the same way rather than blocking the whole send.
optional_picture_names |= {
    "Onset_ECMWF_climatology", "Onset_ECMWF_climatology_10mm",
    "median_wet_climatology", "wet5_climatology", "wet7_climatology",
}

# Picture-only shapes (no AI narration) — matched by shape name or alt text,
# wherever in the deck that shape happens to live.
for slide in prs.slides:
    for shape in list(slide.shapes):
        key, path = first_mapped(shape, picture_paths)
        if key is None:
            continue
        path = resolve_picture_path(path)
        if os.path.exists(path):
            replace_picture(slide, shape, path)
        elif key in optional_picture_names:
            print(f"WARNING: missing optional picture for '{key}': {path}", file=sys.stderr)
        else:
            print(f"WARNING: missing required picture for '{key}': {path}", file=sys.stderr)
            required_missing.append(key)

dt_obj = datetime.fromisoformat(date_str)
day = dt_obj.day
suffix = ("th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th"))
formatted_date = f"{dt_obj.strftime('%B')} {day}{suffix}, {dt_obj.strftime('%Y')}"

month_abbrevs = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
                  7: "Jul", 8: "Aug", 9: "Sept", 10: "Oct", 11: "Nov", 12: "Dec"}
short_date = f"{day} {month_abbrevs[dt_obj.month]} {dt_obj.strftime('%Y')}"

# AI-narrated slide types. The Drive template no longer places "{type}_text"/
# "{type}_plot" shapes at the same slide index as slide_types (they're
# scattered among many new picture-only slides), so match by name or alt text
# across the whole deck instead of assuming slide i holds slide_types[i].
narrated_text = {f"{t}_text": (t, body) for t, body in zip(slide_types, slide_text)}
narrated_plot = {f"{t}_plot": t for t in slide_types if t in plot_paths}

for slide in prs.slides:
    for shape in list(slide.shapes):
        text_key, spec = first_mapped(shape, narrated_text)
        if spec is not None:
            t, body = spec
            if t == "date":
                set_slide_text(shape, formatted_date, font_size=24, font_name="Karla Medium", align=PP_ALIGN.CENTER)
            elif t == "sum":
                set_slide_text(shape, body, font_size=17)
            else:
                set_slide_text(shape, body, font_size=13)
            continue
        plot_key, t = first_mapped(shape, narrated_plot)
        if t is None:
            continue
        path = plot_paths[t]
        if os.path.exists(path):
            replace_picture(slide, shape, path)
        else:
            print(f"WARNING: missing required picture for '{plot_key}': {path}", file=sys.stderr)
            required_missing.append(plot_key)

# Extra date-bearing shapes that don't follow the "{type}_text" naming
# convention: "gen_date" has a "-date-" placeholder inline in a longer
# sentence, and "sum title" is a short "<day> <Mon> <year> Outlook" heading.
# Both are edited in-place (existing run text) to preserve their template
# formatting rather than going through set_slide_text.
for slide in prs.slides:
    for shape in slide.shapes:
        keys = set(shape_keys(shape))
        if "gen_date" in keys:
            for p in shape.text_frame.paragraphs:
                for run in p.runs:
                    if "-date-" in run.text:
                        run.text = run.text.replace("-date-", formatted_date)
        elif "sum title" in keys:
            runs = shape.text_frame.paragraphs[0].runs
            if runs:
                runs[0].text = f"{short_date} Outlook"
                for run in runs[1:]:
                    run.text = ""

if required_missing:
    print(
        f"ERROR: {len(required_missing)} required picture(s) missing, refusing to save "
        f"a briefing with stale template placeholders: {', '.join(required_missing)}",
        file=sys.stderr,
    )
    sys.exit(1)

prs.save(f"s2s_briefing_{date_str}.pptx")