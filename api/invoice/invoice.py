import os
import json
import time
import logging
import requests
import azure.functions as func

# ---------- Hilfsfunktionen ----------

def _to_yyyymmdd(s):
    if not s:
        return ""
    try:
        # DI liefert ISO-8601, optional mit 'Z'
        s = s.replace("Z", "")
        # Versuche mehrere Formate
        from datetime import datetime
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.fromisoformat(s).strftime("%Y%m%d")
            except Exception:
                pass
        # Fallback: nur Ziffern rausziehen
        return "".join(ch for ch in s if ch.isdigit())[:8]
    except Exception:
        return ""

def _safe_get(d, *path, default=""):
    cur = d
    try:
        for p in path:
            if isinstance(cur, list):
                cur = cur[p]
            else:
                cur = cur.get(p, {})
        if isinstance(cur, dict):
            return default
        return cur if cur is not None else default
    except Exception:
        return default

def build_yambs(di_doc: dict, pdf_filename: str) -> str:
    """
    Erzeuge K/D/T-Zeilen (YAMBS) aus einem DI-Document (prebuilt-invoice).
    Gibt den kompletten TXT-String zurück.
    """

    # ---- Kopf-Felder aus DI ----
    vendor_name = _safe_get(di_doc, "vendor", "name", default="")
    vendor_vat  = _safe_get(di_doc, "vendor", "vatId", default="")
    invoice_id  = _safe_get(di_doc, "invoiceId", default="")
    invoice_dt  = _to_yyyymmdd(_safe_get(di_doc, "invoiceDate", default=""))
    due_dt      = _to_yyyymmdd(_safe_get(di_doc, "dueDate", default=""))
    currency    = _safe_get(di_doc, "currency", default="EUR")

    total_amount = _safe_get(di_doc, "amounts", "invoiceTotal", "amount", default=0) or 0
    tax_amount   = _safe_get(di_doc, "amounts", "totalTax", "amount", default=0) or 0

    street = _safe_get(di_doc, "vendor", "address", "streetAddress", default="")
    city   = _safe_get(di_doc, "vendor", "address", "city", default="")

    # ---- K-Zeile aufbauen (angepasst an dein Beispiel) ----
    # Wir füllen relevante Spalten und polstern mit leeren Feldern
    k = [
        "K",                     #  1 Kennzeichen
        "",                      #  2
        "",                      #  3 (optional: Kreditor-ID aus SAP)
        "",                      #  4
        vendor_name,             #  5 Lieferantenname
        invoice_dt,              #  6 Rechnungsdatum (YYYYMMDD)
        currency,                #  7 Währung
        f"{float(total_amount):.2f}".replace(".", ","),  # 8 Brutto
        f"{0:.2f}".replace(".", ","),                   # 9 Skonto (0)
    ]
    while len(k) < 17:
        k.append("")
    k.append(f"{float(tax_amount):.2f}".replace(".", ","))  # 17 Steuerbetrag

    while len(k) < 21:
        k.append("")
    k.append(invoice_dt)   # 21 Beleg-/Leistungsdatum
    k.append(invoice_id)   # 22 Rechnungsnummer

    while len(k) < 42:
        k.append("")
    k.append(vendor_vat)   # 43 UID

    while len(k) < 45:
        k.append("")
    k.append(pdf_filename) # 45 PDF-Dateiname

    k.append("0101")       # 46 Dokumenttyp

    while len(k) < 58:
        k.append("")
    k.append(invoice_dt)   # 59 Eingangsdatum (hier = Rechnungsdatum)

    while len(k) < 64:
        k.append("")
    k.append(street)       # 65 Straße

    while len(k) < 68:
        k.append("")
    k.append(city)         # 69 Stadt

    while len(k) < 90:
        k.append("")

    k_line = ";".join(k)

    # ---- D/T-Zeilen (Positionen) ----
    lines = [ "V;;YAMBS.Invoice;", k_line ]

    items = di_doc.get("items", []) or []
    pos = 1
    for it in items:
        qty   = _safe_get(it, "quantity", "value", default=1) or 1
        price = _safe_get(it, "unitPrice", "amount", default=0) or 0
        total = _safe_get(it, "amount", "amount", default=0) or 0
        desc  = _safe_get(it, "description", default="")

        d = [
            "D",
            str(pos),                                 # Pos-Nr
            "",                                       # Artikelnummer (leer)
            f"{float(qty):.2f}".replace(".", ","),    # Menge
            "",                                       # frei
            "",                                       # frei
            f"{float(price):.2f}".replace(".", ","),  # Einzelpreis
            f"{float(total):.2f}".replace(".", ","),  # Gesamtpreis
        ]
        while len(d) < 19:
            d.append("")
        d.append("1")  # 19: Steuercode "1" (Beispiel – anpassen, falls nötig)
        while len(d) < 40:
            d.append("")
        lines.append(";".join(d))

        t = [
            "T",
            "",
            f"{pos:04d}",   # Textblocknummer
            desc
        ]
        while len(t) < 10:
            t.append("")
        lines.append(";".join(t))

        pos += 1

    return "\n".join(lines)


def _cors_headers():
    origin = os.getenv("ALLOWED_ORIGIN", "*")
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Allow-Methods": "OPTIONS,POST"
    }

# ---------- Hauptfunktion ----------

def invoice(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP POST:
      Body: PDF (binary)
      Header: X-PDF-Filename (optional)
    Response: JSON { fields, yambsTxt }
    """
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_cors_headers())

    try:
        endpoint = os.environ["FORMREC_ENDPOINT"].rstrip("/")
        key      = os.environ["FORMREC_KEY"]
        model_id = os.getenv("MODEL_ID", "prebuilt-invoice")
    except KeyError as ex:
        msg = f"Missing app setting: {ex}"
        logging.error(msg)
        return func.HttpResponse(msg, status_code=500, headers=_cors_headers())

    pdf_bytes = req.get_body()
    if not pdf_bytes:
        return func.HttpResponse("No PDF in request body.", status_code=400, headers=_cors_headers())

    pdf_filename = req.headers.get("X-PDF-Filename", "invoice.pdf")

    try:
        # ---- 1) Analyze aufrufen ----
        analyze_url = f"{endpoint}/documentintelligence/documentModels/{model_id}:analyze?api-version=2023-10-31-preview"
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/pdf"
        }
        r = requests.post(analyze_url, headers=headers, data=pdf_bytes, timeout=60)
        if r.status_code != 202:
            return func.HttpResponse(
                f"Analyze call failed: {r.status_code} {r.text}",
                status_code=502,
                headers=_cors_headers()
            )

        # ---- 2) Polling bis 'succeeded' --------
        op_url = r.headers.get("Operation-Location")
        if not op_url:
            return func.HttpResponse("No Operation-Location header.", status_code=502, headers=_cors_headers())

        for _ in range(60):   # bis ~60 Sekunden
            pr = requests.get(op_url, headers={"Ocp-Apim-Subscription-Key": key}, timeout=30)
            data = pr.json()
            status = data.get("status")
            if status in ("succeeded", "failed"):
                break
            time.sleep(1.0)

        if status != "succeeded":
            return func.HttpResponse(f"DI status: {status}\n{json.dumps(data)[:2000]}", status_code=502, headers=_cors_headers())

        docs = data.get("analyzeResult", {}).get("documents", [])
        if not docs:
            return func.HttpResponse("No document recognized.", status_code=200, headers=_cors_headers())

        di_doc = docs[0].get("fields", {}) or {}

        # ---- 3) YAMBS TXT generieren ----
        yambs_txt = build_yambs(di_doc, pdf_filename)

        # ---- 4) Felder für UI komprimiert zurückgeben ----
        summary = {
            "invoiceId":  di_doc.get("invoiceId"),
            "invoiceDate": di_doc.get("invoiceDate"),
            "currency": di_doc.get("currency"),
            "total": _safe_get(di_doc, "amounts", "invoiceTotal", "amount", default=None),
            "totalTax": _safe_get(di_doc, "amounts", "totalTax", "amount", default=None),
            "vendor": {
                "name": _safe_get(di_doc, "vendor", "name", default=None),
                "vatId": _safe_get(di_doc, "vendor", "vatId", default=None),
                "street": _safe_get(di_doc, "vendor", "address", "streetAddress", default=None),
                "city": _safe_get(di_doc, "vendor", "address", "city", default=None)
            },
            "itemsCount": len(di_doc.get("items", []) or [])
        }

        body = {
            "ok": True,
            "summary": summary,
            "yambsTxt": yambs_txt
        }
        return func.HttpResponse(
            json.dumps(body, ensure_ascii=False),
            status_code=200,
            mimetype="application/json",
            headers=_cors_headers()
        )

    except Exception as ex:
        logging.exception("invoice() failed")
        return func.HttpResponse(f"Error: {ex}", status_code=500, headers=_cors_headers())