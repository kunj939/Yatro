from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0.00")

# Symbols shown to travellers. Currencies without a widely-recognised symbol fall back to their code plus a
# space (e.g. "AED 120"), which is clearer than guessing a symbol that would be ambiguous or wrong.
CURRENCY_SYMBOLS: dict[str, str] = {
    "INR": "₹", "USD": "$", "GBP": "£", "EUR": "€", "JPY": "¥", "CNY": "¥", "KRW": "₩", "VND": "₫",
    "THB": "฿", "PHP": "₱", "TRY": "₺", "ILS": "₪", "UAH": "₴", "PLN": "zł", "CHF": "CHF ",
    "AUD": "A$", "NZD": "NZ$", "CAD": "C$", "HKD": "HK$", "SGD": "S$", "MYR": "RM", "TWD": "NT$",
    "IDR": "Rp", "PKR": "₨", "BDT": "৳", "LKR": "₨", "NPR": "₨",
    "AED": "AED ", "SAR": "SAR ", "QAR": "QAR ", "KWD": "KWD ", "BHD": "BHD ", "OMR": "OMR ",
    "ZAR": "R", "EGP": "E£", "NGN": "₦", "KES": "KSh",
    "BRL": "R$", "MXN": "MX$", "ARS": "AR$",
    "SEK": "kr", "NOK": "kr", "DKK": "kr", "CZK": "Kč", "HUF": "Ft", "RON": "lei",
}

# Roughly how many units of a currency one INR buys, for turning an India-calibrated cost estimate (see
# app/services/fare_model.py) into a plausible order-of-magnitude figure elsewhere. These are NOT live
# exchange rates — Amounts converted this way are always labelled "Estimated"; a real fare from the transport
# or hotel provider itself (see app/providers/google.py) is used in preference to this whenever one exists.
APPROX_INR_TO: dict[str, Decimal] = {
    "USD": Decimal("0.012"), "GBP": Decimal("0.0095"), "EUR": Decimal("0.011"), "JPY": Decimal("1.8"),
    "CNY": Decimal("0.086"), "AUD": Decimal("0.018"), "NZD": Decimal("0.020"), "CAD": Decimal("0.016"),
    "CHF": Decimal("0.011"), "SGD": Decimal("0.016"), "HKD": Decimal("0.094"), "AED": Decimal("0.044"),
    "SAR": Decimal("0.045"), "QAR": Decimal("0.044"), "THB": Decimal("0.43"), "MYR": Decimal("0.056"),
    "IDR": Decimal("190"), "PHP": Decimal("0.68"), "VND": Decimal("290"), "KRW": Decimal("16.2"),
    "ZAR": Decimal("0.22"), "EGP": Decimal("0.59"), "TRY": Decimal("0.41"), "BRL": Decimal("0.066"),
    "MXN": Decimal("0.24"), "PKR": Decimal("3.3"), "BDT": Decimal("1.3"), "LKR": Decimal("3.6"),
    "NPR": Decimal("1.6"), "NOK": Decimal("0.13"), "SEK": Decimal("0.13"), "DKK": Decimal("0.082"),
    "PLN": Decimal("0.048"), "CZK": Decimal("0.27"), "HUF": Decimal("4.3"),
}


def D(value) -> Decimal:
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_rupees(value) -> Decimal:
    """Estimates are shown in whole units of the trip's currency; ledger/split amounts keep the 2 decimals."""
    return Decimal(str(value or 0)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def to_currency(amount_inr, currency: str) -> Decimal:
    """An India-calibrated cost estimate (fare_model.py works entirely in rupee terms), converted to a rough
    figure in `currency`. Identity for INR or an unrecognised code — better a plausible rupee-scale number
    than a crash, and it stays visibly "Estimated" either way."""
    if currency == "INR" or currency not in APPROX_INR_TO:
        return D(amount_inr)
    return D(D(amount_inr) * APPROX_INR_TO[currency])


def fmt_money(value, currency: str = "INR") -> str:
    v = D(value)
    whole = int(v)
    s = str(abs(whole))
    if currency == "INR":
        if len(s) > 3:
            head, tail = s[:-3], s[-3:]
            groups = []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            if head:
                groups.insert(0, head)
            s = ",".join(groups + [tail])
    else:
        # ordinary 3-digit grouping for everyone else
        groups = []
        while len(s) > 3:
            groups.insert(0, s[-3:])
            s = s[:-3]
        groups.insert(0, s)
        s = ",".join(groups)
    frac_units = abs(v) - abs(whole)
    frac = f".{int(frac_units * 100):02d}" if frac_units else ""
    symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} ")
    return f"{'-' if v < 0 else ''}{symbol}{s}{frac}"


def fmt_inr(value) -> str:
    return fmt_money(value, "INR")