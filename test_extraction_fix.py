from niryat_mcp import _extract_hs_code


CASE_A = """# Cotton T-Shirts – HS Code

Great question! **Cotton t-shirts fall under HS Chapter 61** (knitted or crocheted apparel).

## Specific HS Code:

**HS Code: 6109** – *T-shirts, singlets and other vests, knitted or crocheted*

For India, the extended **ITC-HS code is 8 digits**. Common sub-codes for cotton t-shirts include:

- **61091000** – T-shirts of cotton (knitted)
- **61092000** – T-shirts of man-made fibres (if synthetic)
"""

CASE_B = """## Primary HS Code: **61091000**

**HS Code:** 61091000\\
**Product Description:** T-shirts, knit, of cotton
"""


assert _extract_hs_code(CASE_A) == "61091000"
assert _extract_hs_code(CASE_B) == "61091000"
print("Case A extraction: 61091000")
print("Case B extraction: 61091000")
print("PASS: both captured responses extract 61091000")
