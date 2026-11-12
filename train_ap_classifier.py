import os

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder


df = pd.read_csv("syntheic_data/synthetic_ap_data.csv")

FEATURES = [
    "invoice_amount",
    "amount_vs_vendor_avg_ratio",
    "vendor_is_first_time",
    "is_round_number",
    "is_duplicate_invoice_number",
    "gst_rate_pct",
    "interstate",
    "payment_terms_days",
    "days_outstanding",
    "overdue",
]
X = df[FEATURES].copy()
# Cast interstate, overdue, vendor_is_first_time, is_round_number, and
# is_duplicate_invoice_number to int.
for column in [
    "interstate",
    "overdue",
    "vendor_is_first_time",
    "is_round_number",
    "is_duplicate_invoice_number",
]:
    X[column] = X[column].astype(int)

label_encoder = LabelEncoder()
y = label_encoder.fit_transform(df["decision"])

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

# class_weight="balanced" required: approve is ~89% of data, unweighted
# model would always predict approve and still look 89% accurate
model = RandomForestClassifier(
    n_estimators=200,
    max_depth=8,
    class_weight="balanced",
    random_state=42,
)
model.fit(X_train, y_train)

y_pred = model.predict(X_test)
print(classification_report(y_test, y_pred, target_names=label_encoder.classes_))

os.makedirs("models/finance", exist_ok=True)
joblib.dump(model, "models/finance/ap_classifier.pkl")
joblib.dump(label_encoder, "models/finance/ap_label_encoder.pkl")
