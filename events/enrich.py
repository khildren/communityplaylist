"""
Event enrichment helpers — category detection and genre tagging
based on event title, description, and venue name.
"""
import re

# ── Category detection ────────────────────────────────────────────────────────

CATEGORY_RULES = [
    # (category_code, [keyword patterns]) — checked in order, first match wins
    ('arts', [
        r'\bcomedy\b', r'\bstand.?up\b', r'\bimprov\b', r'\bimprovisation\b',
        r'\bsketch\b', r'\bcomedian\b', r'\bcomics?\b',
        r'\btheater\b', r'\btheatre\b', r'\bplay\b.*\bstage\b', r'\bstage\b.*\bplay\b',
        r'\bmusical\b', r'\bdrama\b', r'\bperformance art\b',
        r'\bspoken word\b', r'\bpoetry\b', r'\bpoem\b', r'\bslam\b.*\bpoet',
        r'\bfilm\b.*\bscreen', r'\bscreening\b', r'\bdocumentary\b',
        r'\bart\b.*\bshow\b', r'\bart\b.*\bexhibit', r'\bgallery\b.*\bopen',
        r'\bopen.*\bgallery\b', r'\bexhibition\b', r'\binstallation\b.*\bart',
        r'\blecture\b', r'\bpanel\b.*\bdiscussion', r'\btalk\b.*\bpresent',
        r'\bworkshop\b', r'\bclass\b.*\bart', r'\bdance\b.*\bperform',
    ]),
    ('bike', [
        r'\bbike\b', r'\bbikes\b', r'\bbicycle\b', r'\bcycling\b', r'\bride\b',
        r'\bpedal\b', r'\bcriti?cal mass\b', r'\bpedalpalooza\b', r'\bshift\b.*bike',
        r'\btwo.?wheel', r'\bvelodrome\b', r'\bbmx\b',
    ]),
    ('fund', [
        r'\bfundraiser\b', r'\bfundraising\b', r'\bbenefit\b', r'\bcharity\b',
        r'\bdonat(e|ion)\b', r'\bauction\b', r'\bgala\b', r'\bsilent auction\b',
        r'\braise.*fund', r'\bfund.*raise',
    ]),
    ('food', [
        r'\bfood\b', r'\bfarm.?market\b', r'\bfarmers.?market\b', r'\bdinner\b',
        r'\bsupper\b', r'\blunch\b', r'\bbrunch\b', r'\btasting\b', r'\bbrew(ery|fest|pub)\b',
        r'\bbeer\b', r'\bwine\b', r'\bwhisky\b', r'\bwhiskey\b', r'\bspirits\b',
        r'\bcocktail\b', r'\bpop.?up.*dinner', r'\bsupper.?club\b', r'\bnight.?market\b',
    ]),
    ('music', [
        r'\bconcert\b', r'\blive music\b', r'\blive.*show\b', r'\bshow\b.*\bband\b',
        r'\bband\b', r'\bdj\b', r'\bd\.j\.\b', r'\bfestival\b', r'\bfest\b',
        r'\bperform(ance|s|ing)\b', r'\btour\b', r'\balbum\b', r'\brelease.?show\b',
        r'\bopen.?mic\b', r'\bjam\b.*\bsession\b', r'\bkaraoke\b', r'\bopera\b',
        r'\bsymphony\b', r'\borchestra\b', r'\bchoir\b', r'\bbluegrass\b',
        r'\bjazz\b', r'\bblues\b', r'\brap\b', r'\bhip.?hop\b', r'\brock\b.*\bshow',
        r'\belectronic\b.*\bmusic', r'\btechno\b', r'\bhouse music\b', r'\brave\b',
        r'\brecital\b',
    ]),
    ('hybrid', [
        r'\bhybrid\b', r'\bin.person.*online\b', r'\bonline.*in.person\b',
        r'\bvirtual.*live\b', r'\blive.*virtual\b', r'\bstreamed\b.*\bvenue\b',
    ]),
]

# Venue name patterns that imply a category
VENUE_CATEGORY = [
    ('music',  [r'studio', r'lounge', r'ballroom', r'theater', r'theatre',
                r'hall', r'arena', r'amphitheater', r'venue', r'bar$', r'club$']),
    ('food',   [r'market', r'brewery', r'taproom', r'kitchen', r'cafe', r'bistro',
                r'farm\b', r'vineyard', r'winery']),
    ('bike',   [r'velo', r'cycle', r'bicycle', r'bike shop', r'bicycle shop']),
]


def detect_category(title, description='', location=''):
    """Return the best-guess Event category code, or '' if unknown."""
    text = f'{title} {description[:300]} {location}'.lower()

    for category, patterns in CATEGORY_RULES:
        for pat in patterns:
            if re.search(pat, text, re.I):
                return category

    # Fallback: check venue name only
    loc = location.lower()
    for category, patterns in VENUE_CATEGORY:
        for pat in patterns:
            if re.search(pat, loc, re.I):
                return category

    return ''


# ── Genre detection ───────────────────────────────────────────────────────────

GENRE_KEYWORDS = {
    'Jazz':          [r'\bjazz\b'],
    'Blues':         [r'\bblues\b'],
    'Hip-Hop':       [r'\bhip.?hop\b', r'\brap\b', r'\brap music\b'],
    'Drum and Bass': [r'\bdrum[ -]and[ -]bass\b', r'\bd&b\b', r'\bdnb\b'],
    'Electronic':    [r'\belectronic music\b', r'\btechno\b', r'\bhouse music\b', r'\bEDM\b',
                      r'\bDJ set\b', r'\bambient music\b',
                      r'\bpsytrance\b', r'\btrance\b'],
    'Folk':          [r'\bfolk\b', r'\bacoustic\b', r'\bbluegrass\b', r'\bcountry\b'],
    'Classical':     [r'\bclassical\b', r'\borchestra\b', r'\bsymphony\b',
                      r'\bopera\b', r'\bchamber\b', r'\brecital\b', r'\bchoir\b',
                      r'\bchoral\b'],
    'R&B':           [r'\br&b\b', r'\brhythm.?and.?blues\b', r'\bsoul\b'],
    'Punk':          [r'\bpunk\b', r'\bpop.?punk\b', r'\bhard.?core\b', r'\bhardcore\b'],
    'Metal':         [r'\bmetal\b', r'\bblack metal\b', r'\bdeath metal\b', r'\bdoom\b'],
    'Indie':         [r'\bindie\b', r'\balternative\b', r'\balt.rock\b'],
    'Pop':           [r'\bpop\b(?! up)(?! art)'],
    'Rock':          [r'\brock\b(?! climbing)(?! wall)', r'\bgarage\b.*\bband',
                      r'\bpsychedelic\b'],
    'Reggae':        [r'\breggae\b', r'\bdub\b', r'\bska\b'],
    'World Music':   [r'\bworld music\b', r'\blatino\b', r'\bafrobeat\b',
                      r'\bflamenc[oa]\b', r'\bklezmer\b', r'\bceltic\b'],
    'Experimental':  [r'\bexperimental\b', r'\bavant.?garde\b', r'\bnoise\b.*\bmusic'],
}


def detect_genres(title, description=''):
    """Return list of genre name strings that match the event text."""
    text = f'{title} {description[:500]}'.lower()
    matched = []
    for genre, patterns in GENRE_KEYWORDS.items():
        for pat in patterns:
            if re.search(pat, text, re.I):
                matched.append(genre)
                break
    return matched


def enrich_event(event, geocode=True, save=True):
    """
    Fill in missing category, genres, lat/lng, and neighborhood on an Event instance.
    Only overwrites fields that are blank/None.
    """
    from events.models import Genre
    from events.geocode import geocode_location, reverse_geocode_neighborhood

    changed = False

    # Category
    if not event.category:
        cat = detect_category(event.title, event.description, event.location)
        if cat:
            event.category = cat
            changed = True

    # Geocode + neighborhood
    if geocode and event.location and not event.location.startswith(('http', 'www')):
        if event.latitude is None or event.longitude is None:
            lat, lng = geocode_location(event.location)
            if lat:
                event.latitude = lat
                event.longitude = lng
                changed = True

        if event.latitude and not event.neighborhood:
            hood = reverse_geocode_neighborhood(event.latitude, event.longitude)
            if hood:
                event.neighborhood = hood
                changed = True

    if changed and save:
        fields = ['category', 'latitude', 'longitude', 'neighborhood']
        event.save(update_fields=fields)

    # Genres (separate M2M — always attempt if category is music)
    if event.category == 'music' and event.pk and not event.genres.exists():
        genre_names = detect_genres(event.title, event.description)
        if genre_names:
            for name in genre_names:
                genre, _ = Genre.objects.get_or_create(name=name)
                event.genres.add(genre)

    return changed
