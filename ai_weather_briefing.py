from google import genai
from google.genai import types
import io
import os
import sys
import get_ECMWF_functions as gef
from datetime import datetime, timedelta
from pptx import Presentation
from pptx.util import Pt
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
import json
import shutil
import subprocess
import requests

prefix=os.environ["MAIN_PATH"]

# Google Slides template (weather_briefing_kenya_template). Primary fetch is
# the public "anyone with the link" pptx export. rclone/Drive API are fallbacks
# if the file is made private. Override with BRIEFING_TEMPLATE_ID,
# GOOGLE_DRIVE_TOKEN, or BRIEFING_TEMPLATE_PATH. GOOGLE_API_KEY is Gemini-only.
TEMPLATE_SLIDES_ID = os.environ.get(
    "BRIEFING_TEMPLATE_ID", "1zSp3C35PqDfMKbT8WtEcxoG2EoyIAJA5"
)
TEMPLATE_EXPORT_URL = (
    f"https://docs.google.com/presentation/d/{TEMPLATE_SLIDES_ID}/export/pptx"
)
PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
SLIDES_MIME = "application/vnd.google-apps.presentation"
DRIVE_FILE_URL = (
    f"https://www.googleapis.com/drive/v3/files/{TEMPLATE_SLIDES_ID}"
    "?fields=id,name,mimeType&supportsAllDrives=true"
)
DRIVE_EXPORT_URL = (
    f"https://www.googleapis.com/drive/v3/files/{TEMPLATE_SLIDES_ID}/export"
    f"?mimeType={PPTX_MIME}&supportsAllDrives=true"
)
DRIVE_MEDIA_URL = (
    f"https://www.googleapis.com/drive/v3/files/{TEMPLATE_SLIDES_ID}"
    "?alt=media&supportsAllDrives=true"
)

# Briefing date is today. Original ECMWF/GEFS products are published ~2 days
# late and live under that lagged init; skills plots use DATE_STR (today).
ECMWF_LAG_DAYS = 2


def _iso_today():
    return datetime.today().strftime("%Y-%m-%d")


def _lagged_date(date_str, days=ECMWF_LAG_DAYS):
    return (datetime.fromisoformat(date_str) - timedelta(days=days)).strftime(
        "%Y-%m-%d"
    )


def _resolve_dates():
    date_str = os.environ.get("DATE_STR") or _iso_today()
    if "ECMWF_DATE_STR" in os.environ:
        ecmwf_date_str = os.environ["ECMWF_DATE_STR"]
    elif os.path.isdir(f"plots/Kenya/{date_str}"):
        # Historical / explicit DATE_STR rerun: original plots are under that date.
        ecmwf_date_str = date_str
    else:
        ecmwf_date_str = _lagged_date(date_str)
    return date_str, ecmwf_date_str


date_str, ecmwf_date_str = _resolve_dates()
print(
    f"Briefing date {date_str}; ECMWF/GEFS product date {ecmwf_date_str}",
    file=sys.stderr,
)

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
        promt_unformat1,
        f"{prefix}/data/{ecmwf_date_str}/rainfall_onset_s2s_Kenya.nc",
    )
    gef.save_dict(promt_unformat1, f"{prefix}/promt_unformat1.json")
except Exception as exc:
    print(f"add_onset_from_netcdf failed: {exc}")

# promt_unformat= promt_unformat1 | promt_unformat2 | promt_unformat3
# _ecmwf_lag_note = ""
# if ecmwf_date_str != date_str:
#     _ecmwf_lag_note = (
#         f"ECMWF / GEFS products are from init {ecmwf_date_str} "
#         f"({ECMWF_LAG_DAYS}-day publication lag).\n"
#     )
# user_prompt = f"""
# Forecast date: {date_str}
# {_ecmwf_lag_note}Country: Kenya
# Month: {date_str[5:7]}
# Zone statistics (6-week forecast).
# Onset dates come from the rainfall-onset action (first 3-day spell of at least 20 mm with no 7 consecutive days below 1 mm in the next 21 days), median over ensemble members and grid cells in each region. Use them in one sentence of the Overall Summary for **OND Short Rains** onset (typical mid-October). Never describe MAM Long Rains onset. Do not infer onset from weekly totals. Each forecast slide (2–10) should be 2–3 sentences only.
# {gef.format_prompt_data(promt_unformat)}
# """

# if os.environ.get("GOOGLE_API_KEY"):
#     client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

#     response = client.models.generate_content(
#         model="gemini-3.1-flash-lite",
#         contents=user_prompt,
#         config=types.GenerateContentConfig(
#             system_instruction=system_prompt,
#             max_output_tokens=2500,
#         )
#     )
#     summary = response.text
# else:
#     # if google api key is missing, skip AI synthesis and use placeholder text
#     print(
#         "WARNING: GOOGLE_API_KEY not set; skipping AI synthesis and using "
#         "placeholder slide text",
#         file=sys.stderr,
#     )

placeholder = "[AI summary skipped]"
summary = "---SLIDE---".join([date_str] + [placeholder] * 9)

# var_ex='''\n \nLegend:\np33= Percentage of ensemble members below normal of model climate
# p66= Percentage of ensemble members above normal of model climate
# p50anom= Anomaly of the ensemble mean from the median of the model climate in % and mm
# efi= Extreme forecast index'''

# with open(f'{prefix}/prompts/digest_{date_str}.txt', 'w') as f:
#     f.write(summary)

def _rclone_remote():
    return os.environ.get("BRIEFING_RCLONE_REMOTE", "gdrive")


def _rclone_refresh(remote):
    subprocess.run(
        ["rclone", "about", f"{remote}:"],
        check=False,
        capture_output=True,
        text=True,
    )


def _rclone_drive_token(remote="gdrive"):
    _rclone_refresh(remote)
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
        return _rclone_drive_token(_rclone_remote())
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


def _drive_http_error(op, resp):
    body = (resp.text or "").strip().replace("\n", " ")
    return f"Drive API {op} {resp.status_code}: {body[:400]}"


class _GoogleAuthSession(requests.Session):
    """Keep Bearer auth on googleusercontent / googleapis download redirects.

    requests strips Authorization when the host changes, which makes Drive
    answer 403 "The request is missing a valid API key."
    """

    def rebuild_auth(self, prepared_request, response):
        return


def _rclone_copy_template(dest_path):
    remote = _rclone_remote()
    dest = os.path.abspath(dest_path)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    _rclone_refresh(remote)
    proc = subprocess.run(
        [
            "rclone",
            "--drive-export-formats",
            "pptx",
            "backend",
            "copyid",
            f"{remote}:",
            TEMPLATE_SLIDES_ID,
            dest,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not os.path.isfile(dest):
        detail = (proc.stderr or proc.stdout or "rclone copyid failed").strip()
        raise RuntimeError(detail)
    with open(dest, "rb") as f:
        head = f.read(2)
    if head != b"PK":
        raise RuntimeError(f"rclone copyid did not produce a pptx ({dest})")
    return dest_path


def _drive_api_download(dest_path, token):
    session = _GoogleAuthSession()
    session.headers["Authorization"] = f"Bearer {token}"
    meta = session.get(DRIVE_FILE_URL, timeout=120)
    if not meta.ok:
        raise RuntimeError(_drive_http_error("files.get", meta))
    mime = meta.json().get("mimeType", "")
    url = DRIVE_EXPORT_URL if mime == SLIDES_MIME else DRIVE_MEDIA_URL
    resp = session.get(url, timeout=120)
    if not resp.ok:
        raise RuntimeError(_drive_http_error("download", resp))
    return _write_pptx(dest_path, resp.content, f"Drive API ({mime or 'unknown'})")


def download_slides_template(dest_path):
    errors = []

    try:
        resp = requests.get(TEMPLATE_EXPORT_URL, timeout=120)
        resp.raise_for_status()
        return _write_pptx(dest_path, resp.content, TEMPLATE_EXPORT_URL)
    except Exception as exc:
        errors.append(f"public export: {exc}")
        print(
            f"WARNING: public Slides export failed ({exc}); trying rclone/Drive",
            file=sys.stderr,
        )

    if shutil.which("rclone"):
        try:
            return _rclone_copy_template(dest_path)
        except Exception as exc:
            errors.append(f"rclone: {exc}")
            print(f"WARNING: rclone template copy failed ({exc})", file=sys.stderr)

    token = None
    try:
        token = _drive_access_token()
    except Exception as exc:
        errors.append(f"token: {exc}")
        print(f"WARNING: could not get a Drive token ({exc})", file=sys.stderr)

    if token:
        try:
            return _drive_api_download(dest_path, token)
        except Exception as exc:
            errors.append(f"Drive API: {exc}")
            print(f"WARNING: Drive API template download failed ({exc})", file=sys.stderr)

    raise RuntimeError("Drive template download failed: " + "; ".join(errors))


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
    """Exact match: shape name or alt-text title/description equals a mapping key.

    A trailing ``.png`` on the name/alt text is ignored so a placeholder named
    ``kenya_weekly_rainfall_analog_years.png`` still maps to that plot stem.
    """
    for key in shape_keys(shape):
        stem = key[:-4] if key.lower().endswith(".png") else key
        if stem in mapping:
            return stem, mapping[stem]
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

kenya_path = f"plots/Kenya/{ecmwf_date_str}"
great_horn_path = f"plots/Great_Horn/{ecmwf_date_str}"
diagnostics_path = f"plots/diagnostics/{ecmwf_date_str}/weekly"
diagnostics_monthly_path = f"plots/diagnostics/{ecmwf_date_str}/monthly"
briefing_plots_path = f"plots/briefing/{date_str}/"

# only the first len(plots) slide types have a "{type}_plot" shape
plot_paths = {t: f"{kenya_path}/weekly/{p}.png" for t, p in zip(slide_types, plots)}

# Indian Ocean moisture diagnostics (see IndianOceanState.py)
IOD_path = f"{diagnostics_path}/ECMWF_s2s_10wind_sst_anomaly_{ecmwf_date_str}.png"
IO_ivt_weekly_path = f"{diagnostics_path}/ECMWF_s2s_ivt_u_{ecmwf_date_str}.png"
IO_ivt_monthly_path = f"{diagnostics_monthly_path}/ECMWF_s2s_ivt_u_{ecmwf_date_str}.png"
IO_TCWV_anom_path = f"{diagnostics_path}/ECMWF_s2s_tcw_anomaly_{ecmwf_date_str}.png"
IO_precip_anom_path = f"{diagnostics_path}/ECMWF_s2s_precip_anomaly_{ecmwf_date_str}.png"
IO_precip_anom_std_path = f"{diagnostics_path}/ECMWF_s2s_precip_std_anomaly_{ecmwf_date_str}.png"

# rainy season onset maps (see run_rainfall_onset.py) -- wet-spell/no-dry-spell
# definition, the ICPAC 10 mm wet-spell variant, and the two-stage
# cumulative-rainfall ("accum") definition. Generator stems use icpac10mm,
# not _10mm.
onsetecmwf_path = f"{kenya_path}/monthly/onset_s2s.png"
onsetgefs_path = f"{kenya_path}/monthly/onset_gefs.png"
onsetecmwf_accum_path = f"{kenya_path}/monthly/onset_s2s_accum.png"
onsetgefs_accum_path = f"{kenya_path}/monthly/onset_gefs_accum.png"
onsetecmwf_10mm_path = f"{kenya_path}/monthly/onset_s2s_icpac10mm.png"
onsetgefs_10mm_path = f"{kenya_path}/monthly/onset_gefs_icpac10mm.png"
onsetecmwf_accum_clim_path = f"{kenya_path}/monthly/onset_s2s_climatology_accum.png"

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

# downscaled counterparts (Kenya only): onset maps from the per-member daily
# downscaled forecast (downscaled block in run_rainfall_onset.py), and dry/wet
# spell + weekly >20mm chance maps (gef.plot_downscaled_spell_maps /
# gef.plot_downscaled_exceedance, called from dowscale_dekade.py).
# downscaled_onset_icpac is the default 20mm wet-spell definition.
downscaled_picture_paths = {
    "downscaled_onset_icpac": f"{kenya_path}/monthly/onset_downscaled.png",
    "downscaled_onset_icpac10mm": f"{kenya_path}/monthly/onset_downscaled_icpac10mm.png",
    "downscaled_onset_accum": f"{kenya_path}/monthly/onset_downscaled_accum.png",
    "dwn_wet_5": f"{kenya_path}/monthly/prob_wetspell_5days_downscaled.png",
    "dwn_wet_7": f"{kenya_path}/monthly/prob_wetspell_7days_downscaled.png",
    "dwn_wet_median": f"{kenya_path}/monthly/median_wetspell_length_downscaled.png",
    "dwn_dry_5": f"{kenya_path}/monthly/prob_dryspell_5days_downscaled.png",
    "dwn_dry_7": f"{kenya_path}/monthly/prob_dryspell_7days_downscaled.png",
    "dwn_dry_median": f"{kenya_path}/monthly/median_dryspell_length_downscaled.png",
    "chance_20mm": f"{kenya_path}/weekly/weekly_chance_higherthan_20mm_downscaled.png",
}

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
    **downscaled_picture_paths,
}

# Plots generated by ws_scripts/*.sh and collected into briefing_plots_path
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
    "kenya_ond_last_week_rainfall_kenya_extent",
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
    "kenya_aifs_chirps_verify_5mm",
    "kenya_aifs_chirps_bias",
    "kenya_aifs_chirps_mae",
    "kenya_ecmwf_chirps_verify_5mm",
    "kenya_ecmwf_chirps_bias",
    "kenya_ecmwf_chirps_mae",
    "kenya_kmsa_chirps_verify_5mm",
    "kenya_kmsa_chirps_bias",
    "kenya_kmsa_chirps_mae",
    "kenya_cumulus_chirps_verify_5mm",
    "kenya_cumulus_chirps_bias",
    "kenya_cumulus_chirps_mae",
    "sst_global_oisst_nino_iod",
    "kenya_week1_mae_vs_chirps_4wk",
    "kenya_daily_downscaled_precip",
    "kenya_aifs_daily_precip",
    "kenya_gefs_daily_precip",
    "kenya_daily_downscaled_precip_anomaly",
    "kenya_aifs_daily_precip_anomaly",
    "kenya_gefs_daily_precip_anomaly",
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


_BLANK_PICTURE_PNG = None


def _blank_picture_bytes():
    """Missing plot placeholder cached once and reused"""
    global _BLANK_PICTURE_PNG
    if _BLANK_PICTURE_PNG is None:
        from PIL import Image, ImageDraw, ImageFont  # already a python-pptx dependency

        img = Image.new("RGB", (1200, 800), color=(230, 230, 230))
        draw = ImageDraw.Draw(img)
        text = "No plot available"
        font = ImageFont.load_default(size=64)
        bbox = draw.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((1200 - w) / 2, (800 - h) / 2), text, fill=(150, 150, 150), font=font)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        _BLANK_PICTURE_PNG = buf.getvalue()
    return _BLANK_PICTURE_PNG


def blank_picture_stream():
    return io.BytesIO(_blank_picture_bytes())


def _cNvPr(shape):
    for el in shape._element.iter():
        if el.tag.endswith("}cNvPr"):
            return el
    return None


def replace_picture(slide, shape, image_path, preserve_aspect=True):
    """Swap the bitmap but keep name / alt text so later matching still works.

    preserve_aspect=True (default): keep template top-left corner fixed,
    scale image as far as possible to fit template box without distorting.

    preserve_aspect=False: stretch to fill template box exactly; can distort.
    """
    left, top, box_width, box_height = shape.left, shape.top, shape.width, shape.height
    name = getattr(shape, "name", None)
    cnv = _cNvPr(shape)
    title = cnv.get("title") if cnv is not None else None
    descr = cnv.get("descr") if cnv is not None else None
    shape._element.getparent().remove(shape._element)
    if preserve_aspect:
        pic = slide.shapes.add_picture(image_path, left, top)
        scale = min(box_width / pic.width, box_height / pic.height)
        pic.width = int(pic.width * scale)
        pic.height = int(pic.height * scale)
    else:
        pic = slide.shapes.add_picture(image_path, left, top, box_width, box_height)
    if name:
        pic.name = name
    new_cnv = _cNvPr(pic)
    if new_cnv is not None:
        if title:
            new_cnv.set("title", title)
        if descr:
            new_cnv.set("descr", descr)
    return pic

# Missing pictures that should block the whole send (rather than going out with a
# stale template placeholder in that slide) are tracked here and checked just
# before prs.save() below.
required_missing = []

# ws_scripts/*.sh-sourced pictures are still being wired into this pipeline
# (see populate_briefing_template3.py) and aren't reliably populated every day yet,
# so a missing one is tolerated (warn, keep the template placeholder) rather than
# blocking the send like the core forecast/diagnostic pictures below.
# 10 mm / climatology onset maps are not on every GCS date yet (they landed
# after 2026-09-09). Warn and keep the template placeholder instead of
# blocking the send.
optional_onset_names = {
    "Onset_ECMWF_10mm",
    "Onset_GEFS_10mm",
    "Onset_ECMWF_accum_climatology",
}
optional_picture_names = set(briefing_plot_names) | optional_onset_names

# Reforecast-archive climatology plots are new and depend on an extra network
# fetch (Planette's reforecast archive) on top of the core pipeline, so a
# missing one is tolerated the same way rather than blocking the whole send.
optional_picture_names |= {
    "Onset_ECMWF_climatology", "Onset_ECMWF_climatology_10mm",
    "median_wet_climatology", "wet5_climatology", "wet7_climatology",
}

# The Great Horn tercile plot is regional context, not core to the Kenya
# briefing, and needs a Great_Horn plot run (or a GCS top-up) on top of the
# Kenya pipeline, so a missing one shouldn't block the send either.
optional_picture_names.add("ECMWF_tercile_plot_EA")

# Downscaled onset/spell/exceedance plots are new and each generator step
# runs inside its own try/except, so a missing one is tolerated the same way.
optional_picture_names |= set(downscaled_picture_paths)

# Picture-only shapes (no AI narration). Fill only when the shape name or
# alt text equals a plot stem exactly (e.g. kenya_weekly_rainfall_analog_years).
# Unlabeled pictures are left as template placeholders.
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
            replace_picture(slide, shape, blank_picture_stream(), preserve_aspect=False)
        else:
            print(f"WARNING: missing required picture for '{key}': {path}", file=sys.stderr)
            required_missing.append(key)
            replace_picture(slide, shape, blank_picture_stream(), preserve_aspect=False)

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
            replace_picture(slide, shape, blank_picture_stream(), preserve_aspect=False)

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
    msg = (
        f"{len(required_missing)} required picture(s) missing, would save a "
        f"briefing with blank placeholders in their place: {', '.join(required_missing)}"
    )
    if os.environ.get("BRIEFING_ALLOW_MISSING_PICTURES"):
        # for testing, we want to save the deck even if some plots are missing
        print(f"WARNING: {msg} — saving anyway (BRIEFING_ALLOW_MISSING_PICTURES set)", file=sys.stderr)
    else:
        print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(1)

prs.save(f"s2s_briefing_{date_str}.pptx")