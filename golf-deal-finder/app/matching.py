"""
Product matching: collapse messy retailer listings into one comparable SKU.

This is the hard part of a price-comparison app. Five retailers sell the exact
same driver and describe it five different ways:

    "TaylorMade Qi35 Driver - 10.5* Stiff RH"
    "TaylorMade Golf Qi35 Driver (Right Hand, 10.5 Degree, Stiff Flex)"
    "2025 TaylorMade Qi35 Men's Driver 10.5 S-Flex w/ Ventus Blue"

All three are the same thing. If you don't collapse them, your app shows the
user three separate products instead of one product with three prices, which
defeats the entire point.

Strategy, in priority order:
  1. GTIN/UPC match. Rare in affiliate feeds but authoritative when present.
  2. Structured key: brand + club type + model + the variant attributes that
     make a club a distinct SKU (loft, flex, dexterity, set composition).
  3. Fuzzy fallback: token-set similarity on the leftover model string, gated
     on brand + club type + loft already agreeing, so we can never merge a
     9-degree driver into a 10.5.

Condition is deliberately NOT part of the identity key -- it's carried on the
offer. An open-box Qi35 is the same product at a different price, and the UI
decides whether to show it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# --------------------------------------------------------------------------
# Vocabularies
# --------------------------------------------------------------------------

# Canonical brand -> aliases seen in the wild. Order matters for longest match:
# "Scotty Cameron" must beat "Titleist" on a "Titleist Scotty Cameron" title.
BRAND_ALIASES: dict[str, list[str]] = {
    "Scotty Cameron": ["scotty cameron", "scotty"],
    "Vokey": ["vokey", "vokey design", "titleist vokey"],
    "TaylorMade": ["taylormade", "taylor made", "tmag", "taylormade golf"],
    "Callaway": ["callaway", "callaway golf"],
    "Odyssey": ["odyssey"],
    "Titleist": ["titleist"],
    "PING": ["ping"],
    "Cobra": ["cobra", "cobra golf", "puma cobra", "cobra puma"],
    "Mizuno": ["mizuno", "mizuno golf"],
    "Srixon": ["srixon"],
    "Cleveland": ["cleveland", "cleveland golf"],
    "Wilson": ["wilson", "wilson staff"],
    "Tour Edge": ["tour edge"],
    "XXIO": ["xxio"],
    "Honma": ["honma"],
    "Bridgestone": ["bridgestone", "bridgestone golf"],
    "Bettinardi": ["bettinardi"],
    "L.A.B. Golf": ["l.a.b. golf", "lab golf", "l.a.b."],
    "Sub 70": ["sub 70", "sub70"],
}

# Club type -> patterns. Checked in this order; first hit wins, so the more
# specific types (fairway, hybrid) come before the generic ones.
CLUB_TYPE_PATTERNS: list[tuple[str, str]] = [
    ("golf_balls", r"\b(golf\s+balls?|balls?)\b|\bdozen\b|\b1\s*dz\b"),
    # Single irons MUST be caught before the set pattern. Retailers sell
    # individual irons and full sets under nearly identical titles, and a
    # $170 single iron merged into a $1,400 set produces a $1,230 "saving"
    # that does not exist. This is the most damaging thing the matcher can
    # get wrong, because the number looks spectacular and is a lie.
    # The bare singular "Iron" is the common case and the easy one to miss:
    # "TaylorMade P790 Iron" at $149 sits right beside "P790 Irons" at $1,399.
    # The negative lookahead keeps "Iron Set" out of this bucket -- without it
    # every set in the catalogue would be classified as a single club.
    ("single_iron", r"\b(single|individual)\s+irons?\b|\b#?\d\s*-?\s*iron\b"
                    r"|\biron\b(?!\s*sets?\b)"
                    r"|\b(pw|gw|aw|sw|lw)\s*(wedge\s*)?only\b"),
    ("iron_set", r"\biron\s*sets?\b|\birons\b|\b\d\s*-\s*(pw|gw|aw|sw)\b"),
    ("wedge", r"\bwedges?\b|\b(lob|sand|gap|pitching)\s+wedge\b"),
    ("putter", r"\bputters?\b"),
    ("driver", r"\bdrivers?\b"),
    ("fairway_wood", r"\bfairway\b|\b(3|5|7|9)\s*wood\b|\b(3|5|7|9)w\b|\bheavenwood\b"),
    ("hybrid", r"\bhybrids?\b|\brescue\b|\butility\b"),
    ("bag", r"\b(stand|cart|carry|staff)\s+bag\b|\bgolf\s+bag\b"),
    ("glove", r"\bglove\b"),
    ("shoes", r"\bshoes?\b|\bfootjoy\b"),
    ("rangefinder", r"\brangefinder\b|\blaser\b|\bgps\s+watch\b"),
]

# Shaft families worth capturing -- a Qi35 with a Ventus Black is genuinely a
# different SKU (and a different price) than one with the stock Ventus Blue.
SHAFT_BRANDS = [
    "ventus black", "ventus blue", "ventus red", "ventus",
    "hzrdus smoke", "hzrdus black", "hzrdus red", "hzrdus",
    "tensei av", "tensei ck", "tensei",
    "kai'li", "kaili", "kai li",
    "diamana", "aldila ascent", "aldila rogue", "aldila",
    "project x hzrdus", "project x lz", "project x",
    "kbs tour", "kbs $-taper", "kbs c-taper", "kbs max", "kbs",
    "dynamic gold 120", "dynamic gold 105", "dynamic gold", "dg tour issue",
    "nippon modus", "modus3", "modus 3", "modus",
    "recoil dart", "recoil esx", "recoil",
    "graphite design tour ad", "graphite design", "tour ad",
    "denali blue", "denali black", "denali red", "denali",
    "atmos", "speeder nx", "speeder", "autoflex",
    "alta cb", "alta j cb", "alta distanza", "alta",
    "elevate mph", "elevate tour", "elevate",
    "rogue silver", "rogue black",
    "steelfiber",
]

FLEX_CANONICAL = {
    "x-stiff": "X", "xstiff": "X", "extra stiff": "X", "tour x": "X",
    "x flex": "X", "x-flex": "X", "xflex": "X", "tx": "TX", "x-stiff flex": "X",
    "stiff": "S", "s flex": "S", "s-flex": "S", "sflex": "S", "firm": "S",
    "regular": "R", "r flex": "R", "r-flex": "R", "rflex": "R",
    "senior": "A", "a flex": "A", "a-flex": "A", "amateur": "A", "light": "A",
    "ladies": "L", "womens": "L", "women's": "L", "lady": "L", "l flex": "L",
}

# Words that carry no identity. Stripped before the model string is compared.
NOISE_WORDS = {
    "golf", "new", "brand", "mens", "men's", "men", "womens", "women's", "women",
    "the", "with", "w", "and", "for", "in", "custom", "authorized", "dealer",
    "free", "shipping", "sale", "clearance", "closeout", "deal", "special",
    "genuine", "official", "premium", "series", "edition", "model", "club",
    "clubs", "head", "handed", "hand", "right", "left", "rh", "lh", "flex",
    "degree", "degrees", "deg", "loft", "shaft", "graphite", "steel", "adult",
    "assembled", "usa", "brand-new", "instock", "stock",
}

CONDITION_PATTERNS: list[tuple[str, str]] = [
    ("open_box", r"\bopen\s*box\b|\bob\b"),
    ("refurbished", r"\brefurb(ished)?\b|\brecertified\b"),
    ("used", r"\bused\b|\bpre-?owned\b|\bsecond\s*hand\b"),
    ("demo", r"\bdemo\b|\bfloor\s*model\b"),
]

# Used-gear grades, mapped onto one scale. Every seller invents their own
# wording -- 2nd Swing's "Excellent" is not GlobalGolf's "Excellent" is not a
# marketplace seller's "Mint" -- and making those comparable is the whole
# premise of the product. This is the vocabulary layer; the per-seller
# calibration (what each ACTUALLY means) comes later, from their own grading
# guides, and belongs in a per-seller mapping table.
#
# Ordered longest-first: "like new" must beat "new", "very good" beat "good".
CONDITION_GRADES: list[tuple[str, str]] = [
    (r"brand\s*new", "New"),
    (r"like\s*new", "Like New"),
    (r"very\s*good", "Very Good"),
    (r"near\s*mint", "Mint"),
    (r"mint", "Mint"),
    (r"excellent", "Excellent"),
    (r"good", "Good"),
    (r"average", "Good"),
    (r"fair", "Fair"),
    (r"poor", "Poor"),
    (r"below\s*average", "Poor"),
]

# The grade a seller states, ranked. Lower index = better condition. Used for
# sorting and for "cheapest at this grade or better" queries.
GRADE_ORDER = ["New", "Like New", "Mint", "Excellent", "Very Good",
               "Good", "Fair", "Poor"]

# Should the shaft be part of a product's identity?
#
# Argument for: a Qi35 with an aftermarket Ventus Black really is a different
# SKU at a different price, and merging them makes your cheapest price a lie.
# Argument against: retailers are wildly inconsistent about naming the *stock*
# shaft. One lists "Qi35 Driver", the next lists "Qi35 Driver w/ Ventus Blue"
# for the identical club. Put shaft in the key and you split one product into
# two, each showing a single price -- which defeats the whole app.
#
# Inconsistent naming is far more common than aftermarket listings, so shaft
# stays OUT of the key and is shown per-offer in the compare table instead. The
# shopper sees "Ventus Black" on the $749 row and understands the gap. Flip
# this to True if your feeds turn out to be disciplined about shaft naming.
SHAFT_IN_KEY = False

# Plausible loft ranges, used only when a listing gives a bare number with no
# degree marker ("Wedge 56 RH"). Without ranges we'd read the 35 in "Qi35" or
# the 11 in "Phantom 11" as a loft. Single irons are deliberately absent: the
# number in "7 iron" is the iron, not the loft.
BARE_LOFT_RANGES: dict[str, tuple[float, float]] = {
    "driver": (7.0, 13.5),
    "fairway_wood": (12.0, 25.0),
    "hybrid": (15.0, 30.0),
    "wedge": (46.0, 65.0),
}

# A number standing on its own -- not glued to letters, so "Qi35" and "SM10"
# and "G440" can't be mistaken for specs.
_STANDALONE_NUM_RE = re.compile(r"(?<![A-Za-z0-9.])(\d{1,2}(?:\.\d)?)(?![A-Za-z0-9.])")

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_LOFT_RE = re.compile(
    r"(?<![\d.])(\d{1,2}(?:\.\d)?)\s*(?:°|\*|\bdeg\b|\bdegrees?\b)", re.I
)
# Set composition. Sellers write "4-PW" and also "4-P", "5-G", "3-PW,AW".
# The single-letter forms are common enough that missing them leaves the
# composition unparsed, which puts a 4-iron-through-pitching-wedge set in the
# same bucket as one that starts at 6.
_SET_RE = re.compile(
    r"\b(\d)\s*-\s*(pw|gw|aw|sw|lw|p|g|a|s|w|\d)"
    r"(?:\s*[,+]\s*(pw|gw|aw|sw|lw|p|g|a|s|w))?\b", re.I
)

# Single letters sellers use as shorthand for the top club in a set.
_SET_LETTER = {"P": "PW", "G": "GW", "A": "AW", "S": "SW", "W": "PW"}

# Two-digit model years: "P790 '23", "Qi '25". Common in seller titles and
# invisible to a four-digit year pattern.
_YEAR2_RE = re.compile(r"[‘’'`](\d{2})\b")
_BOUNCE_RE = re.compile(r"\b(\d{2}(?:\.\d)?)\s*[.\-/]\s*(\d{1,2})\b")
_LENGTH_RE = re.compile(r"\b(3[2-6])\s*(?:\"|in\b|inch\b)", re.I)
_DOZEN_RE = re.compile(r"\b(\d+)\s*(?:dozen|dz|doz)\b|\bdozen\b", re.I)


# --------------------------------------------------------------------------
# Result type
# --------------------------------------------------------------------------

@dataclass
class ParsedProduct:
    """What we managed to pull out of one retailer's listing."""

    brand: str | None = None
    club_type: str | None = None
    model: str = ""
    loft: float | None = None
    flex: str | None = None
    dexterity: str | None = None
    shaft: str | None = None
    set_composition: str | None = None
    bounce: str | None = None
    length: float | None = None
    condition: str = "new"
    grade: str | None = None   # normalised used-gear grade, e.g. "Excellent"
    year: int | None = None
    gtin: str | None = None
    mpn: str | None = None
    match_key: str = ""
    display_name: str = ""
    tokens: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["tokens"] = sorted(self.tokens)
        return d


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _ascii_fold(text: str) -> str:
    """Kai'li -> Kai'li, but curly quotes and accents normalized away."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def _slug(text: str) -> str:
    text = _ascii_fold(text).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def normalize_gtin(raw: str | None) -> str | None:
    """
    Strip formatting and pad UPC-A to a 13-digit GTIN so a retailer sending a
    12-digit UPC matches one sending the EAN-13 form of the same product.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) not in (8, 12, 13, 14):
        return None
    if len(digits) == 12:
        digits = "0" + digits
    if len(digits) == 14 and digits.startswith("0"):
        digits = digits[1:]
    return digits


# --------------------------------------------------------------------------
# Attribute extraction
# --------------------------------------------------------------------------

def extract_brand(title: str, feed_brand: str | None = None) -> str | None:
    """
    Prefer the brand the feed declares, but only after mapping it through the
    alias table -- feeds say "TAYLORMADE GOLF" and "Taylor Made" for one brand.
    Falls back to scanning the title.
    """
    haystacks = [h for h in (feed_brand, title) if h]
    for haystack in haystacks:
        low = " " + _ascii_fold(haystack).lower() + " "
        best: tuple[int, str] | None = None
        for canonical, aliases in BRAND_ALIASES.items():
            for alias in aliases:
                if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", low):
                    if best is None or len(alias) > best[0]:
                        best = (len(alias), canonical)
        if best:
            return best[1]
    if feed_brand:
        return feed_brand.strip().title()
    return None


def extract_club_type(title: str, category: str | None = None) -> str | None:
    blob = f"{title} {category or ''}".lower()
    for club_type, pattern in CLUB_TYPE_PATTERNS:
        if re.search(pattern, blob, re.I):
            return club_type
    return None


def extract_loft(title: str, club_type: str | None = None) -> float | None:
    """
    Explicit degree markers win. Failing that, fall back to a bare number, but
    only one that falls in a plausible range for this club type -- most
    retailers write "Wedge 56 RH" with no marker at all, and that listing has
    to match the one that writes "Wedge 56°".
    """
    m = _LOFT_RE.search(title)
    if m:
        value = float(m.group(1))
        if 4.0 <= value <= 72.0:
            return value

    if club_type not in BARE_LOFT_RANGES:
        return None
    low, high = BARE_LOFT_RANGES[club_type]

    # Strip the things that also look like bare numbers.
    text = _SET_RE.sub(" ", title)
    text = _LENGTH_RE.sub(" ", text)
    text = _DOZEN_RE.sub(" ", text)
    text = _YEAR_RE.sub(" ", text)

    # Wedges are often written "56.10" -- loft then bounce, not a decimal loft.
    if club_type == "wedge":
        bm = _BOUNCE_RE.search(text)
        if bm:
            loft = float(bm.group(1))
            if low <= loft <= high:
                return loft

    for candidate in _STANDALONE_NUM_RE.findall(text):
        value = float(candidate)
        if low <= value <= high:
            return value
    return None


def extract_flex(title: str) -> str | None:
    low = " " + _ascii_fold(title).lower() + " "
    # Longest alias first so "x-stiff" is not read as "stiff".
    for alias in sorted(FLEX_CANONICAL, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", low):
            return FLEX_CANONICAL[alias]
    # Bare single-letter flex in a parenthetical or after a slash: "(10.5/S)"
    m = re.search(r"[(/,\-]\s*([RSXAL])\s*(?:flex)?\s*[)/,\-]", title, re.I)
    if m:
        return m.group(1).upper()
    return None


def extract_dexterity(title: str) -> str | None:
    low = _ascii_fold(title).lower()
    if re.search(r"\bleft[\s-]*hand(ed)?\b|\blh\b|\(lh\)|\blefty\b", low):
        return "LH"
    if re.search(r"\bright[\s-]*hand(ed)?\b|\brh\b|\(rh\)", low):
        return "RH"
    return None


def extract_shaft(title: str) -> str | None:
    low = _ascii_fold(title).lower().replace("'", "'")
    for shaft in SHAFT_BRANDS:  # already ordered specific -> generic
        if shaft.replace("'", "'") in low:
            return shaft.title()
    # Fallback for shafts not in the list: "... w/ Denali 10 Regular RH".
    # The list will never be complete, and an unrecognized shaft name left in
    # the title ends up in the model string and splits the product.
    m = re.search(r"\bw/\s*([A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,2})", title)
    if m:
        candidate = m.group(1).strip()
        words = [w for w in candidate.split() if w.lower() not in NOISE_WORDS
                 and w.lower() not in FLEX_CANONICAL]
        if words:
            return " ".join(words).title()
    return None


def extract_set_composition(title: str) -> str | None:
    m = _SET_RE.search(title)
    if not m:
        return None
    start, end, extra = m.group(1), m.group(2).upper(), m.group(3)
    end = _SET_LETTER.get(end, end)
    comp = f"{start}-{end}"
    if extra:
        extra = extra.upper()
        comp += f",{_SET_LETTER.get(extra, extra)}"
    return comp


def extract_condition(title: str, feed_condition: str | None = None) -> str:
    blob = f"{title} {feed_condition or ''}"
    for condition, pattern in CONDITION_PATTERNS:
        if re.search(pattern, blob, re.I):
            return condition
    return "new"


def extract_grade(title: str, feed_condition: str | None = None) -> str | None:
    """
    Pull the seller's condition wording onto one scale: "Excellent",
    "Very Good", and so on. Returns None when no grade is stated, which is the
    normal case for new gear.
    """
    blob = f"{title} {feed_condition or ''}"
    for pattern, grade in CONDITION_GRADES:   # already longest-first
        if re.search(rf"(?<![a-z]){pattern}(?![a-z])", blob, re.I):
            return grade
    return None


def extract_model(
    title: str, brand: str | None, club_type: str | None, loft: float | None = None
) -> str:
    """
    Strip everything that isn't the model name: the brand, the attributes we
    already parsed into their own fields, and the marketing noise. What's left
    should be "qi35" or "gt2" or "jpx925 hot metal".
    """
    text = _ascii_fold(title)

    if brand:
        for alias in BRAND_ALIASES.get(brand, [brand.lower()]):
            text = re.sub(rf"(?i)(?<![a-z]){re.escape(alias)}(?![a-z])", " ", text)
        text = re.sub(rf"(?i)(?<![a-z]){re.escape(brand)}(?![a-z])", " ", text)

    # Remove parsed attributes so they can't pollute the model string. The bare
    # loft matters most here: leave "10.5" in and one retailer's model becomes
    # "qi35 10.5" while another's stays "qi35", and the product splits in two.
    text = _LOFT_RE.sub(" ", text)
    if loft is not None:
        for form in {f"{loft:g}", f"{loft:.1f}", f"{int(loft)}" if loft.is_integer() else ""}:
            if form:
                text = re.sub(
                    rf"(?<![A-Za-z0-9.]){re.escape(form)}(?![A-Za-z0-9])", " ", text
                )
    text = _BOUNCE_RE.sub(" ", text)
    text = _SET_RE.sub(" ", text)
    text = _LENGTH_RE.sub(" ", text)
    text = _DOZEN_RE.sub(" ", text)
    text = _YEAR_RE.sub(" ", text)
    for shaft in SHAFT_BRANDS:
        text = re.sub(rf"(?i){re.escape(shaft)}", " ", text)
    # Anything still hanging off a "w/" is a shaft we don't have listed. Specs
    # were parsed into fields above, so dropping to end-of-string is safe here
    # and keeps an unknown shaft name out of the model.
    text = re.sub(r"\bw/\s*[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,2}", " ", text)
    for alias in FLEX_CANONICAL:
        text = re.sub(rf"(?i)(?<![a-z]){re.escape(alias)}(?![a-z])", " ", text)
    for _, pattern in CONDITION_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.I)
    # Grade words describe wear, not the club. Leave "Excellent" in and the
    # used listing becomes a separate product from the new one.
    for pattern, _ in CONDITION_GRADES:
        text = re.sub(rf"(?i)(?<![a-z]){pattern}(?![a-z])", " ", text)

    # Club-type words are captured separately; drop them from the model string
    # so "Qi35 Driver" and "Qi35" agree.
    if club_type:
        for ct, pattern in CLUB_TYPE_PATTERNS:
            if ct == club_type:
                text = re.sub(pattern, " ", text, flags=re.I)

    # Anything in brackets is almost always spec restatement or marketing.
    text = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", text)
    text = re.sub(r"[^A-Za-z0-9.+']+", " ", text)

    words = [w for w in text.lower().split() if w and w not in NOISE_WORDS]
    words = [w for w in words if not re.fullmatch(r"[rsxal]", w)]
    # Bare numbers are NOT dropped here. They are usually the model -- Phantom
    # 11, i230, P790, TP5x. Dropping them merged Phantom 5 into Phantom 11,
    # which is exactly the over-merge this matcher exists to prevent. Real
    # specs (loft, bounce, set, length, year) were stripped explicitly above.

    # De-dupe while preserving order: "p790 p790" -> "p790"
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return " ".join(out).strip()


# --------------------------------------------------------------------------
# The parser
# --------------------------------------------------------------------------

def parse(
    title: str,
    *,
    brand: str | None = None,
    category: str | None = None,
    condition: str | None = None,
    gtin: str | None = None,
    mpn: str | None = None,
) -> ParsedProduct:
    """Turn one retailer listing into a structured, comparable product."""
    title = (title or "").strip()

    p = ParsedProduct()
    p.gtin = normalize_gtin(gtin)
    p.mpn = (mpn or "").strip().upper() or None
    p.brand = extract_brand(title, brand)
    p.club_type = extract_club_type(title, category)
    p.loft = extract_loft(title, p.club_type)
    p.flex = extract_flex(title)
    p.dexterity = extract_dexterity(title)
    p.shaft = extract_shaft(title)
    p.set_composition = extract_set_composition(title)
    p.condition = extract_condition(title, condition)
    p.grade = extract_grade(title, condition)
    p.model = extract_model(title, p.brand, p.club_type, p.loft)

    ym = _YEAR_RE.search(title)
    if ym:
        year = int(ym.group(0))
        if 2005 <= year <= 2035:
            p.year = year
    if p.year is None:
        ym2 = _YEAR2_RE.search(title)
        if ym2:
            year = 2000 + int(ym2.group(1))
            if 2005 <= year <= 2035:
                p.year = year

    if p.club_type == "wedge":
        bm = _BOUNCE_RE.search(title)
        if bm:
            p.bounce = f"{bm.group(1)}/{bm.group(2)}"
    if p.club_type == "putter":
        lm = _LENGTH_RE.search(title)
        if lm:
            p.length = float(lm.group(1))

    p.match_key = build_match_key(p)
    p.display_name = build_display_name(p)
    p.tokens = set(p.model.split())
    return p


def build_match_key(p: ParsedProduct) -> str:
    """
    The identity of a SKU. Two listings with the same key are the same thing.

    Note what is and isn't included. Loft, flex, dexterity and set composition
    change the SKU, so they're in. Condition, price, retailer and year are not
    -- a retailer tagging "2025 Qi35" and one tagging plain "Qi35" are selling
    the same club.
    """
    if p.gtin:
        return f"gtin:{p.gtin}"

    parts = [
        _slug(p.brand or "unknown"),
        p.club_type or "unknown",
        _slug(p.model) or "unknown",
    ]
    # Balls and bags have no loft/flex; only add variant fields where they mean
    # something, or every ball listing gets a different key.
    if p.club_type in {"driver", "fairway_wood", "hybrid", "wedge", "single_iron"}:
        parts.append(f"l{p.loft:g}" if p.loft is not None else "l?")
    if p.club_type in {"driver", "fairway_wood", "hybrid", "iron_set", "single_iron"}:
        parts.append(f"f{p.flex}" if p.flex else "f?")
    if p.club_type == "iron_set":
        parts.append(f"s{p.set_composition}" if p.set_composition else "s?")

    # Model year, but ONLY for irons.
    #
    # Most clubs get a new name each generation -- Qi10 becomes Qi35, SM9
    # becomes SM10 -- so the name already separates them and adding the year
    # would split one product into "Qi35" and "2025 Qi35" for nothing.
    #
    # Irons are the exception: P790 and T150 keep the same name across
    # generations sold years apart at very different prices. Observed live:
    # one "TaylorMade P790 Irons" product spanning the 2019, 2021, 2023 and
    # 2025 models, $479 to $1,399, presented as a 66% saving. The range was
    # real; calling it one product was not.
    if p.club_type in {"iron_set", "single_iron"} and p.year:
        parts.append(f"y{p.year}")
    if p.club_type == "putter" and p.length:
        parts.append(f"{p.length:g}in")
    if p.club_type not in {"golf_balls", "glove", "shoes", "bag", "rangefinder"}:
        parts.append(p.dexterity or "d?")
    if SHAFT_IN_KEY and p.shaft:
        parts.append(_slug(p.shaft))

    return ":".join(parts)


def build_display_name(p: ParsedProduct) -> str:
    """A clean title for the product page, rebuilt from parsed fields."""
    bits = [p.brand or "", p.model.title() if p.model else ""]
    type_labels = {
        "driver": "Driver", "fairway_wood": "Fairway Wood", "hybrid": "Hybrid",
        "iron_set": "Irons", "single_iron": "Iron", "wedge": "Wedge",
        "putter": "Putter", "golf_balls": "Golf Balls", "bag": "Bag",
        "glove": "Glove", "shoes": "Shoes", "rangefinder": "Rangefinder",
    }
    if p.club_type in type_labels:
        bits.append(type_labels[p.club_type])

    spec: list[str] = []
    if p.set_composition:
        spec.append(p.set_composition)
    if p.loft is not None:
        spec.append(f"{p.loft:g}°")
    if p.bounce:
        spec.append(f"{p.bounce} bounce")
    if p.length:
        spec.append(f'{p.length:g}"')
    if p.flex:
        spec.append({"R": "Regular", "S": "Stiff", "X": "X-Stiff",
                     "TX": "Tour X", "A": "Senior", "L": "Ladies"}.get(p.flex, p.flex))
    if p.dexterity:
        spec.append(p.dexterity)
    # Shaft is deliberately absent -- see SHAFT_IN_KEY. It varies between
    # listings of the same club, so it belongs on the offer row, not the title.

    name = " ".join(b for b in bits if b).strip()
    if spec:
        name += " — " + ", ".join(spec)
    return re.sub(r"\s+", " ", name) or "Unknown product"


# --------------------------------------------------------------------------
# Fuzzy fallback
# --------------------------------------------------------------------------

def similarity(a: ParsedProduct, b: ParsedProduct) -> float:
    """
    How alike are two model strings? Combines token overlap (Jaccard) with
    character-level ratio, so "jpx925 hot metal" vs "jpx 925 hotmetal" still
    scores high despite tokenizing differently.
    """
    if not a.model or not b.model:
        return 0.0
    ta, tb = a.tokens, b.tokens
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    ratio = SequenceMatcher(
        None, a.model.replace(" ", ""), b.model.replace(" ", "")
    ).ratio()
    return 0.5 * jaccard + 0.5 * ratio


def can_merge(a: ParsedProduct, b: ParsedProduct, threshold: float = 0.86) -> bool:
    """
    Guardrail for the fuzzy path. Everything that makes a club physically
    different must already agree -- we only tolerate sloppiness in the model
    *name*. Getting this wrong shows a user a 9° price on a 10.5° club, which
    is worse than showing two separate listings.
    """
    if a.gtin and b.gtin:
        return a.gtin == b.gtin
    if a.brand != b.brand or a.club_type != b.club_type:
        return False
    if a.loft != b.loft or a.flex != b.flex or a.dexterity != b.dexterity:
        return False
    if a.set_composition != b.set_composition:
        return False
    # Irons keep their name across generations, so the year is part of the
    # product. Without this the fuzzy path quietly undoes the year in the key:
    # the model strings are identical, so everything from 2019 to 2025 merges
    # back into one product.
    if a.club_type in {"iron_set", "single_iron"} and (a.year or None) != (b.year or None):
        return False
    if SHAFT_IN_KEY and (a.shaft or None) != (b.shaft or None):
        return False
    return similarity(a, b) >= threshold


class ProductIndex:
    """
    Accumulates parsed listings and assigns each one a canonical product key.

    Exact key match is O(1). Only listings that miss fall through to the fuzzy
    comparison, and even then only against candidates sharing brand + club type
    + loft, so this stays fast as the catalog grows.
    """

    def __init__(self, threshold: float = 0.86) -> None:
        self.threshold = threshold
        self._by_key: dict[str, ParsedProduct] = {}
        self._buckets: dict[tuple, list[str]] = {}

    @staticmethod
    def _bucket(p: ParsedProduct) -> tuple:
        return (p.brand, p.club_type, p.loft, p.flex, p.dexterity)

    def assign(self, p: ParsedProduct) -> str:
        """Return the canonical key this listing belongs to, creating it if new."""
        if p.match_key in self._by_key:
            return p.match_key

        bucket = self._bucket(p)
        for key in self._buckets.get(bucket, []):
            if can_merge(p, self._by_key[key], self.threshold):
                return key

        self._by_key[p.match_key] = p
        self._buckets.setdefault(bucket, []).append(p.match_key)
        return p.match_key

    def canonical(self, key: str) -> ParsedProduct | None:
        return self._by_key.get(key)

    def __len__(self) -> int:
        return len(self._by_key)
