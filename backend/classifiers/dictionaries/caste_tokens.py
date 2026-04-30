"""Hindu caste surname mappings, optimized for UP / eastern UP voter rolls.

Sources: Census + CSDS Lokniti studies + ECI demographic patterns.

⚠ Caveats:
- Many surnames span multiple castes regionally. We mark them AMBIGUOUS so the
  classifier returns Low confidence and the voter goes to the "Unknown Caste"
  review sheet.
- This is rule-based. Manual spot-check is required before drawing conclusions.
"""

from __future__ import annotations


BRAHMIN = {
    "tokens": {
        "शर्मा", "मिश्रा", "मिश्र", "मिसरा", "तिवारी", "त्रिवेदी", "त्रिपाठी", "त्रिपाठि",
        "पांडे", "पांडेय", "पाण्डेय", "पाण्डे",
        "चौबे", "दूबे", "दुबे",
        "ओझा", "पाठक", "भट्ट", "दीक्षित",
        "चतुर्वेदी", "द्विवेदी", "जोशी", "अवस्थी",
        "बाजपेयी", "बाजपेई", "वाजपेयी",
        "शुक्ला", "शुक्ल", "उपाध्याय",
        "पंडित", "पण्डित", "पुजारी", "महाराज",
        "गोस्वामी", "गोसाईं",
        "भारद्वाज", "अग्निहोत्री", "कौशिक", "व्यास",
    },
    "caste_name": "Brahmin",
    "category": "UC",
}

THAKUR_RAJPUT = {
    "tokens": {
        "ठाकुर", "राजपूत", "चौहान", "राठौर", "राठौड़",
        "सेंगर", "बघेल", "चंदेल", "चन्देल",
        "परिहार", "सिसोदिया", "गहलोत", "भदौरिया",
        "तोमर", "सोलंकी", "बैस", "पंवार",
        "जादौन", "गौर",
    },
    "caste_name": "Thakur/Rajput",
    "category": "UC",
}

KAYASTHA = {
    "tokens": {
        "सिन्हा", "श्रीवास्तव", "श्रीवास्तवा", "माथुर",
        "सक्सेना", "निगम", "अस्थाना", "भटनागर",
        "नंदवाना", "वालिया",
    },
    "caste_name": "Kayastha",
    "category": "UC",
}

VAISHYA_BANIA = {
    "tokens": {
        "गुप्ता", "अग्रवाल", "अग्रहरि", "अग्रहरी",
        "जैन", "मित्तल", "सिंगल", "गोयल", "बंसल", "गर्ग",
        "जायसवाल", "जैसवाल",
        "केसरवानी", "बनिया",
        "मेहता", "सेठ", "साहू", "साहु",
        "महाजन", "पोद्दार", "मोदी",
        "रस्तोगी", "मेहरोत्रा", "मेहरा",
        "सोनी", "सर्राफ", "स्वर्णकार",
    },
    "caste_name": "Vaishya/Bania",
    "category": "UC",
}

YADAV = {
    "tokens": {"यादव", "अहीर", "घोसी"},
    "caste_name": "Yadav",
    "category": "OBC",
}

KURMI_PATEL = {
    "tokens": {
        "कुर्मी", "पटेल", "सचान",
        "वर्मा",
        "कटियार", "कानू", "कंकवार",
    },
    "caste_name": "Kurmi/Patel",
    "category": "OBC",
}

KUSHWAHA_MAURYA = {
    "tokens": {
        "कुशवाहा", "मौर्य", "शाक्य", "सैनी",
        "कोइरी", "महतो", "काछी",
    },
    "caste_name": "Kushwaha/Maurya",
    "category": "OBC",
}

LODH = {
    "tokens": {"लोधी", "लोध", "लोढा"},
    "caste_name": "Lodh",
    "category": "OBC",
}

NISHAD_KEVAT = {
    "tokens": {
        "निषाद", "केवट", "मल्लाह",
        "बिंद", "बिन्द", "मांझी",
        "गोंड", "धीमर", "कश्यप",
    },
    "caste_name": "Nishad/Kevat",
    "category": "OBC",
}

KAHAR = {
    "tokens": {"कहार", "कांहार"},
    "caste_name": "Kahar",
    "category": "OBC",
}

PRAJAPATI = {
    "tokens": {"प्रजापति", "कुम्हार", "कुम्भकार"},
    "caste_name": "Prajapati/Kumhar",
    "category": "OBC",
}

LOHAR_VISHWAKARMA = {
    "tokens": {"लोहार", "विश्वकर्मा", "कारीगर", "सुथार"},
    "caste_name": "Vishwakarma/Lohar",
    "category": "OBC",
}

PAL_GADERIYA = {
    "tokens": {"पाल", "गडरिया", "गड़ेरिया", "धनगर"},
    "caste_name": "Pal/Gaderiya",
    "category": "OBC",
}

GUJAR = {
    "tokens": {"गुर्जर", "गूजर"},
    "caste_name": "Gujjar",
    "category": "OBC",
}

JAT = {
    "tokens": {"जाट"},
    "caste_name": "Jat",
    "category": "OBC",
}

PASI = {
    "tokens": {"पासी", "पासवान"},
    "caste_name": "Pasi",
    "category": "SC",
}

CHAMAR_JATAV = {
    "tokens": {
        "जाटव", "अहिरवार", "रैदास", "रविदास",
        "सूर्यवंशी", "भारती",
        "हरिजन", "चमार",
    },
    "caste_name": "Chamar/Jatav",
    "category": "SC",
}

VALMIKI = {
    "tokens": {"बाल्मीकि", "वाल्मीकि", "भंगी", "मेहतर", "लालबेगी"},
    "caste_name": "Valmiki",
    "category": "SC",
}

KHATIK = {
    "tokens": {"खटीक", "खटिक", "सोनकर"},
    "caste_name": "Khatik",
    "category": "SC",
}

DHOBI = {
    "tokens": {"धोबी", "कन्नौजिया", "कनौजिया"},
    "caste_name": "Dhobi",
    "category": "SC",
}

KORI = {
    "tokens": {"कोरी"},
    "caste_name": "Kori",
    "category": "SC",
}


# AMBIGUOUS — known to span multiple castes; classifier returns Low.
AMBIGUOUS_TOKENS: set[str] = {
    "सिंह",          # Thakur, Sikh, Yadav (some), Kurmi, ...
    "चौधरी",         # Vaishya, Pasi, Jat, ...
    "गौतम",          # Kurmi (sometimes), Brahmin (sometimes), Buddhist
    "श्रीवास्तव",     # Kayastha mostly but called Brahmin in some traditions
    "कश्यप",         # OBC Nishad mostly, but a Brahmin gotra too
    "सैनी",           # Kushwaha sometimes, Sikh sometimes
    "शर्मा",          # primarily Brahmin, but Vishwakarma/Lohar use it too
    "वर्मा",          # primarily Kurmi in eastern UP, but used by other castes too
    "साहू",          # Vaishya in some regions, Teli OBC in others
}


# All Hindu caste groups — iterate to build the lookup map below.
ALL_HINDU_CASTES = [
    BRAHMIN, THAKUR_RAJPUT, KAYASTHA, VAISHYA_BANIA,
    YADAV, KURMI_PATEL, KUSHWAHA_MAURYA, LODH, NISHAD_KEVAT,
    KAHAR, PRAJAPATI, LOHAR_VISHWAKARMA, PAL_GADERIYA,
    GUJAR, JAT,
    PASI, CHAMAR_JATAV, VALMIKI, KHATIK, DHOBI, KORI,
]


def _build_lookup() -> dict[str, tuple[str, str]]:
    """token -> (caste_name, category). Tokens shared across castes are
    merged into AMBIGUOUS_TOKENS rather than mapped, so the classifier
    can return Low confidence on them."""
    lookup: dict[str, tuple[str, str]] = {}
    seen_in: dict[str, str] = {}
    for group in ALL_HINDU_CASTES:
        for tok in group["tokens"]:
            if tok in AMBIGUOUS_TOKENS:
                continue
            if tok in seen_in and seen_in[tok] != group["caste_name"]:
                # Shared between castes → fall back to ambiguous
                lookup.pop(tok, None)
                AMBIGUOUS_TOKENS.add(tok)
                continue
            seen_in[tok] = group["caste_name"]
            lookup[tok] = (group["caste_name"], group["category"])
    return lookup


TOKEN_TO_CASTE: dict[str, tuple[str, str]] = _build_lookup()
