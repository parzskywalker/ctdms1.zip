"""Read a photographed / scanned transporter bill into structured lines with Claude (vision + structured output).

Transporter bills in practice: printed tables (Orient Logistics), handwritten Hindi memos (Bhaiji Roadlines, Jai Bhole
Garage), printed forms filled by hand (Parakh Transport, Osho Express), computer invoices (Prakash Goods Carrier).
Column names vary: LR / बिल्टी नं / GR No / Bilty No; cases = नग / डाग / TP / Pkgs / Qty; freight = किराया /
Amount / Freight. The model returns every row it can read; the operator checks them next to the photo before the
cross-check runs, so a misread digit is caught by a person, never paid automatically.

Configuration: CTDMS_ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY) and setting ai.bill_reader = CLAUDE.
Without a key the photo is shown beside an empty grid to type into (same review screen).
"""
from __future__ import annotations

import base64
import io
import logging
import os
from datetime import date

from pydantic import BaseModel, Field

from app.services.errors import BusinessError

log = logging.getLogger("ctdms.billscan")
MODEL = "claude-opus-5-5"
MAX_PAGES = 6
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class ScannedLine(BaseModel):
    lr_no: str | None = Field(None, description="LR / bilty / GR / docket / consignment number exactly as written")
    lr_date: str | None = Field(None, description="Date of this LR as written, normalised to DD-MM-YYYY if possible")
    party_name: str | None = Field(None, description="Consignee / party / receiver name (transliterate Hindi to English)")
    station: str | None = Field(None, description="Destination station / town, as written (abbreviations allowed)")
    cases: int | None = Field(None, description="Number of packages / cases / boxes (नग, डाग, TP, Pkgs, Qty)")
    rate: float | None = Field(None, description="Rate per case if a rate column is present")
    amount: float | None = Field(None, description="Freight amount of this line (किराया / Amount / Freight)")
    remarks: str | None = Field(None, description="Anything else written on the line, e.g. 'cancel', 'mixed'")
    unclear: bool = Field(False, description="True when any value on this line was hard to read")


class ScannedBill(BaseModel):
    transporter_name: str | None = Field(None, description="Name of the transport company issuing the bill")
    bill_no: str | None = Field(None, description="Bill / invoice / memo number of the transporter")
    bill_date: str | None = Field(None, description="Bill date, DD-MM-YYYY")
    period_from: str | None = Field(None, description="First date of the period billed, DD-MM-YYYY, if stated")
    period_to: str | None = Field(None, description="Last date of the period billed, DD-MM-YYYY, if stated")
    total_amount: float | None = Field(None, description="Total amount payable as written on the bill")
    total_cases: int | None = Field(None, description="Total packages / cases as written on the bill")
    gstin: str | None = Field(None, description="Transporter GSTIN if printed")
    lines: list[ScannedLine] = Field(default_factory=list)
    notes: str | None = Field(None, description="Short notes on anything you could not read or that looks wrong")


PROMPT = """This is a freight bill (or a page of one) that an Indian transport company gave to a pharmaceutical \
distributor (CFA) in Chhattisgarh. Extract it exactly.

Rules:
- One entry in `lines` per consignment row of the table, top to bottom, across all pages. Skip header rows, \
totals and blank rows. Rows marked "cancel" still get a line with remarks "cancel" and no amount.
- Column meanings: LR / बिल्टी नं. / Bilty No / GR No / Gr. No. / LR NO = lr_no. डाग / नग / TP / Pkgs / Pkg.S / \
Qty / Quantity / BOX = cases. किराया / Freight / Amount = amount. Rate / Rate Per C/S = rate. Station / Town / \
कहाँ तक = station. पाने वाले का नाम / Party Name / Consignee = party_name.
- Ditto marks (", 〃, —, a long dash or a repeated "4"/"y" mark under a value) mean "same as the row above": \
write the value from the row above.
- Dates: Indian order (day first). "4-Aug-2026", "04/08/26", "4.8.26", "0418" (=04/08) -> 04-08-2026. When the \
year is missing, use the bill's year.
- Numbers: "3000" or "300/-" or "300=00" mean 300 rupees where the column is clearly rupees (300=00 is 300.00). \
Ticks, slashes and check marks next to numbers are not digits.
- Transliterate Hindi names to English (e.g. सम्भव मेडी. -> Sambhav Medi.). Keep abbreviations (MDGR, BKPR, RJN).
- Never invent a value. Leave a field null if it is not on the paper, and set unclear=true when you had to guess \
a digit. Put the bill's own printed totals in total_amount / total_cases (do not compute them yourself)."""


def api_key() -> str | None:
    from app.config import get_settings
    return (os.environ.get("CTDMS_ANTHROPIC_API_KEY") or get_settings().anthropic_api_key
            or os.environ.get("ANTHROPIC_API_KEY"))


def locked() -> bool:
    """Automatic reading uses the paid Anthropic API: locked unless CTDMS_PAID_SERVICES=true."""
    from app.config import get_settings
    return not get_settings().paid_services


def available() -> bool:
    if locked():
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(api_key())


def _image_block(content: bytes, media_type: str) -> dict:
    """Downscale big phone photos (the API limit is ~5 MB / 8000 px; 2000 px keeps handwriting legible)."""
    data, mt = content, media_type
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(content))
        img = ImageOps.exif_transpose(img)
        if max(img.size) > 2200 or len(content) > 3_500_000:
            img.thumbnail((2200, 2200))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=88)
            data, mt = buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - send the original if Pillow cannot handle it
        pass
    return {"type": "image", "source": {"type": "base64", "media_type": mt,
                                        "data": base64.standard_b64encode(data).decode()}}


def content_blocks(files: list[tuple[bytes, str]]) -> list[dict]:
    blocks = []
    for content, media_type in files[:MAX_PAGES]:
        if media_type == "application/pdf":
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                          "data": base64.standard_b64encode(content).decode()}})
        elif media_type in IMAGE_TYPES:
            blocks.append(_image_block(content, media_type))
        else:
            raise BusinessError("Upload photos (JPG / PNG / WEBP) or a PDF of the bill")
    return blocks


def read_bill(files: list[tuple[bytes, str]], hint: str | None = None) -> ScannedBill:
    """Call Claude. Raises BusinessError with a plain-language message when it cannot be used."""
    if not files:
        raise BusinessError("Attach at least one photo or PDF of the bill")
    if not available():
        raise BusinessError("Automatic bill reading is not set up (no CTDMS_ANTHROPIC_API_KEY). Type the lines next "
                            "to the photo instead.")
    import anthropic
    client = anthropic.Anthropic(api_key=api_key(), timeout=180.0, max_retries=2)
    text = PROMPT + (f"\n\nThe operator says this bill is from: {hint}." if hint else "")
    try:
        resp = client.beta.messages.parse(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "high"},
            messages=[{"role": "user", "content": [*content_blocks(files), {"type": "text", "text": text}]}],
            output_format=ScannedBill,
        )
    except anthropic.AuthenticationError as e:
        raise BusinessError("The AI key was refused (CTDMS_ANTHROPIC_API_KEY) - ask the administrator") from e
    except anthropic.RateLimitError as e:
        raise BusinessError("The AI service is busy - try again in a minute") from e
    except anthropic.BadRequestError as e:
        log.warning("Bill scan rejected: %s", e)
        raise BusinessError("The bill could not be read (file too large or unreadable). Try a clearer photo per "
                            "page.") from e
    except anthropic.APIConnectionError as e:
        raise BusinessError("No connection to the AI service - check the internet connection or type the lines") from e
    except anthropic.APIStatusError as e:
        log.warning("Bill scan failed: %s", e)
        raise BusinessError(f"The AI service returned an error ({e.status_code}); type the lines instead") from e
    if resp.stop_reason == "refusal":
        raise BusinessError("The AI service declined to read this file; type the lines instead")
    if resp.stop_reason == "max_tokens":
        raise BusinessError("The bill is too long to read in one go - upload it a page at a time")
    parsed = resp.parsed_output
    if parsed is None:
        raise BusinessError("The bill could not be read; type the lines instead")
    log.info("Bill scan: %s lines (request %s)", len(parsed.lines), getattr(resp, "_request_id", None))
    return parsed


def to_date(s: str | None, default_year: int | None = None) -> date | None:
    from app.services.utils import parse_date
    if not s:
        return None
    try:
        return parse_date(str(s).strip())
    except ValueError:
        return None
