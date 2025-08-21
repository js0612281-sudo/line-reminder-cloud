import csv
import os

PATIENTS_CSV = "patients.csv"

def load_patients():
    if not os.path.exists(PATIENTS_CSV):
        return []
    with open(PATIENTS_CSV, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))
