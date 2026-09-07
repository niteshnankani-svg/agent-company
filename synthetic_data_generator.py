import random

import numpy as np
import pandas as pd
from faker import Faker


fake = Faker("en_IN")
random.seed(42)
np.random.seed(42)

AP_SUPPLIER_CITIES = [
    ("Bhilwara", "Rajasthan"),
    ("Delhi", "Delhi"),
    ("Ulhasnagar", "Maharashtra"),
    ("Ahmedabad", "Gujarat"),
    ("Surat", "Gujarat"),
    ("Kolkata", "West Bengal"),
    ("Nagpur", "Maharashtra"),
    ("Mumbai", "Maharashtra"),
    ("Indore", "Madhya Pradesh"),
]
AR_CUSTOMER_CITIES = [
    (city, "Maharashtra")
    for city in [
        "Barshi",
        "Latur",
        "Pandharpur",
        "Killari",
        "Paranda",
        "Nilanga",
        "Omerga",
        "Mohol",
        "Sangola",
        "Devni",
        "Renapur",
    ]
]
HOME_STATE = "Maharashtra"
GST_RATES = [5, 12, 18]
GST_WEIGHTS = [0.70, 0.20, 0.10]
BUSINESS_SUFFIXES_AP = [
    "Textiles",
    "Fabrics",
    "Garments",
    "Enterprises",
    "Trading Co.",
    "Hosiery",
    "Syntex",
]
BUSINESS_SUFFIXES_AR = [
    "Dresses",
    "Collection",
    "Cloth Stores",
    "Readymade",
    "Vastra Bhandar",
    "Fashion",
    "Sadi Centre",
]


def gen_ap_records(n=2000, n_vendors=150) -> pd.DataFrame:
    vendors = []
    for _ in range(n_vendors):
        city, state = random.choice(AP_SUPPLIER_CITIES)
        vendors.append(
            {
                "name": f"{fake.last_name()} {random.choice(BUSINESS_SUFFIXES_AP)}, {city}",
                "city": city,
                "state": state,
                "avg_invoice": max(3000, np.random.lognormal(mean=10.3, sigma=1.0)),
                "is_first_time": random.random() < 0.08,
            }
        )

    rows = []
    invoice_numbers = set()
    for index in range(n):
        vendor = random.choice(vendors)
        interstate = vendor["state"] != HOME_STATE
        amount = max(
            500,
            round(
                np.random.lognormal(mean=np.log(vendor["avg_invoice"]), sigma=0.4),
                -2,
            ),
        )
        vendor_deviation = amount / vendor["avg_invoice"]
        is_round_number = amount % 10000 == 0
        is_duplicate = bool(invoice_numbers) and random.random() < 0.02
        if is_duplicate:
            invoice_id = random.choice(list(invoice_numbers))
        else:
            invoice_id = f"INV{random.randint(10000, 99999)}"
            invoice_numbers.add(invoice_id)

        gst_rate = random.choices(GST_RATES, weights=GST_WEIGHTS, k=1)[0]
        gst_amount = amount * gst_rate / 100
        payment_terms_days = random.choice([15, 30, 45, 60, 90])
        days_outstanding = int(np.random.exponential(scale=25))
        overdue = days_outstanding > payment_terms_days

        anomaly_types = []
        anomaly_score = 0
        if vendor_deviation > 2.5:
            anomaly_score += 2
            anomaly_types.append("amount_far_above_vendor_average")
        if vendor["is_first_time"] and amount > 50000:
            anomaly_score += 2
            anomaly_types.append("large_first_time_vendor_invoice")
        if is_round_number and amount > 20000:
            anomaly_score += 1
            anomaly_types.append("suspiciously_round_amount")
        if is_duplicate:
            anomaly_score += 3
            anomaly_types.append("duplicate_invoice_number")
        is_anomaly = anomaly_score >= 2
        if is_anomaly:
            decision = "flag"
        elif overdue and days_outstanding > payment_terms_days + 30:
            decision = "hold"
        else:
            decision = "approve"

        rows.append(
            {
                "invoice_id": invoice_id,
                "vendor_name": vendor["name"],
                "vendor_city": vendor["city"],
                "vendor_state": vendor["state"],
                "interstate": interstate,
                "vendor_avg_invoice": round(vendor["avg_invoice"], 2),
                "vendor_is_first_time": vendor["is_first_time"],
                "invoice_amount": amount,
                "amount_vs_vendor_avg_ratio": round(vendor_deviation, 2),
                "is_round_number": is_round_number,
                "is_duplicate_invoice_number": is_duplicate,
                "gst_rate_pct": gst_rate,
                "gst_amount": round(gst_amount, 2),
                "tax_type": "IGST" if interstate else "CGST+SGST",
                "payment_terms_days": payment_terms_days,
                "days_outstanding": days_outstanding,
                "overdue": overdue,
                "is_anomaly": is_anomaly,
                "anomaly_type": ",".join(anomaly_types) if anomaly_types else None,
                "decision": decision,
            }
        )
    return pd.DataFrame(rows)


def gen_ar_records(n=2000) -> pd.DataFrame:
    rows = []
    for index in range(n):
        city, state = random.choice(AR_CUSTOMER_CITIES)
        name = f"{fake.first_name()} {random.choice(BUSINESS_SUFFIXES_AR)}, {city}"
        amount = np.clip(
            round(np.random.lognormal(mean=9.5, sigma=1.6), -1), 1, 400000
        )
        credit_limit = round(amount * random.uniform(1.2, 3.0), -2)
        days_outstanding = int(np.random.exponential(scale=35))
        payment_terms_days = random.choice([15, 30, 45])
        rows.append(
            {
                "customer_id": f"AR{20000 + index}",
                "customer_name": name,
                "customer_city": city,
                "customer_state": state,
                "outstanding_balance": amount,
                "credit_limit": credit_limit,
                "payment_terms_days": payment_terms_days,
                "days_outstanding": days_outstanding,
                "overdue": days_outstanding > payment_terms_days,
                # Filled by credit_risk_xgb.pkl at inference, not synthetic.
                "credit_risk_score": None,
            }
        )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import os

    os.makedirs("syntheic_data", exist_ok=True)
    ap_data = gen_ap_records(n=2000)
    ar_data = gen_ar_records(n=2000)
    ap_data.to_csv("syntheic_data/synthetic_ap_data.csv", index=False)
    ar_data.to_csv("syntheic_data/synthetic_ar_data.csv", index=False)
    print(ap_data.head(5))
    print(ar_data.head(5))
    print(ap_data["decision"].value_counts())
    print(ap_data["is_anomaly"].mean() * 100)
    print(ap_data["overdue"].mean() * 100)
    print(ar_data["overdue"].mean() * 100)
