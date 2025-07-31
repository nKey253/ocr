import re
import json
from io import StringIO
from PIL import Image, ImageFilter, ImageOps
import pytesseract
import pandas as pd
import streamlit as st

# Streamlit Configuration
st.set_page_config(page_title="Invoice OCR & JSON Extractor", layout="wide")

# CSS Styling
st.markdown("""
<style>
.reportview-container, .main { background: #1e1e2f; color: #f0f0f5; }
.stButton>button { background: #3b3b98; color: #fff; border-radius: 6px; }
</style>
""", unsafe_allow_html=True)

# Title
st.title("📄 Invoice OCR & JSON Extractor")

# Helper Functions

def preprocess(img: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(img)
    filt = gray.filter(ImageFilter.MedianFilter())
    return filt.point(lambda p: 0 if p < 140 else 255)

def extract_header(img: Image.Image):
    w, h = img.size
    hdr = img.crop((0, 0, w, int(h * 0.2)))
    txt = pytesseract.image_to_string(hdr, config='--psm 6')
    inv_no = None
    for pat in [r'Invoice\s*No\.?[:\-]?\s*(\d+)', r'Inv#?[:\-]?\s*(\d+)', r'Invoice\s*ID[:\-]?\s*(\d+)']:
        m = re.search(pat, txt, re.I)
        if m:
            inv_no = m.group(1)
            break
    inv_dt = None
    for pat in [r'\d{1,2}/\d{1,2}/\d{2,4}', r'[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}']:
        m = re.search(pat, txt)
        if m:
            inv_dt = m.group(0)
            break
    return inv_no, inv_dt

def extract_vendor(txt: str) -> str:
    """
    Extract vendor by locating 'Seller:' label. If the line below 'Seller:' is 'Client',
    skip that and pick the next non-empty line. Otherwise, capture inline or fallback
    to the first non-numeric line.
    """
    lines = [ln.strip() for ln in txt.splitlines()]
    # 1) Standalone 'Seller:' line
    for i, ln in enumerate(lines):
        if ln.lower().startswith("seller:") and ln.rstrip(':').strip().lower() == "seller":
            # Check subsequent lines
            for nxt in lines[i+1:]:
                if not nxt:
                    continue
                if nxt.lower().startswith("client"):
                    continue
                return nxt
    # 2) Inline 'Seller: Name' or 'Vendor: Name'
    for ln in lines:
        m = re.match(r'^(?:Seller|Vendor)\s*[:\-]\s*(.+)$', ln, re.I)
        if m:
            name = m.group(1).strip()
            if name.lower() != "client":
                return name
    # 3) Fallback: first non-numeric, non-empty line
    for ln in lines:
        if ln and not re.search(r'\d', ln):
            return ln
    return None

def crop(img: Image.Image, top: float, bot: float) -> Image.Image:
    w, h = img.size
    return img.crop((0, int(h*top), w, int(h*bot)))

def parse_entries(img: Image.Image, stop_summary=False) -> list:
    raw = pytesseract.image_to_data(img, output_type=pytesseract.Output.STRING, config='--psm 6')
    df = pd.read_csv(StringIO(raw), sep='\t').dropna(subset=['text'])
    df = df.sort_values(['block_num', 'par_num', 'line_num', 'word_num'])
    lines = df.groupby('line_num')['text'].apply(lambda ws: ' '.join(ws)).tolist()
    ents = []
    for ln in lines:
        if stop_summary and 'SUMMARY' in ln.upper():
            break
        if re.match(r'^\s*\d+\.\s+', ln):
            ents.append(ln.strip())
        elif ents:
            ents[-1] += ' ' + ln.strip()
    return ents

def extract_line_items(img: Image.Image) -> list:
    """
    Extract line items by pulling out the first six numeric tokens from each entry:
    [item_no, quantity, unit_price, net_worth, vat, gross_worth].
    This ignores digits in the description so that only the intended values are captured.
    """
    ents = parse_entries(crop(img, 0.2, 0.8), stop_summary=True)
    items = []
    x=0
    for entry in ents:
        # Remove 'each' keyword to avoid extra tokens
        clean = re.sub(r"\beach\b", "", entry, flags=re.IGNORECASE)
        nums = re.findall(r"(?:\d+(?:[.,]\d+)+|\d+\s\d+|\d+%)", entry)

        x+=1
        print(nums)
        item_no     = x
        quantity    = nums[0]
        unit_price  = nums[1]
        net_worth   = nums[2]
        vat         = f"{nums[3]}%"
        gross_worth = nums[4]
        # Clean description: strip leading number and any numeric tokens
        desc = re.sub(r"^\s*\d+\.?\s*", "", clean)
        desc = re.sub(r"[\d]+(?:[.,]\d+)?", "", desc)
        desc = re.sub(r"\s+", " ", desc).strip().replace(",", "")
        items.append({
            'item_no':     item_no,
            'description': desc,
            'quantity':    quantity,
            'unit_price':  unit_price,
            'net_worth':   net_worth,
            'vat':         vat,
            'gross_worth': gross_worth
        })
    print(items)
    return items


def extract_summary(img: Image.Image) -> list:
    ents = parse_entries(crop(img, 0.8, 1.0))
    summary = []
    for entry in ents:
        if re.match(r'^Summary', entry, re.I):
            continue
        key, val = (entry.split(':', 1) + [''])[:2]
        summary.append({key.strip(): val.strip()})
    return summary
    
up = st.file_uploader("Upload Invoice", type=["png", "jpg", "jpeg"])
if up:
    img = Image.open(up)
    proc = preprocess(img)
    txt = pytesseract.image_to_string(proc, config='--psm 3')
    vendor = extract_vendor(txt)
    inv_no, inv_dt = extract_header(proc)
    items = extract_line_items(proc)
    summary = extract_summary(proc)

    # Display image and JSON side by side
    col1, col2 = st.columns([1, 1])
    with col1:
        st.subheader("📷 Invoice Image")
        st.image(img, use_container_width=True)
    with col2:
        st.subheader("📝 Extracted JSON")
        st.json({
            'vendor': vendor,
            'invoice_number': inv_no,
            'invoice_date': inv_dt,
            'line_items': items
        })

    # Smaller table for header fields
    st.subheader("Invoice Details")
    header_df = pd.DataFrame([{
        'Vendor': vendor,
        'Invoice Number': inv_no,
        'Invoice Date': inv_dt
    }])
    st.table(header_df)

    # Full line items table
    st.subheader("🔍 Line Items")
    st.dataframe(pd.DataFrame(items), use_container_width=True)

