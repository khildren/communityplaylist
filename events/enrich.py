"""
Event enrichment helpers — category detection and genre tagging
based on event title, description, and venue name.
"""
import re
import html


def clean_text(text, max_len=None):
    """
    Decode HTML entities, strip CSS/style blocks and HTML tags from imported
    event text. Safe to run on both titles and descriptions.
    """
    if not text:
        return text

    # Decode HTML entities: &amp; -> &, &quot; -> ", &#39; -> ', etc.
    text = html.unescape(text)

    # Strip CSS blocks injected by page builders e.g. ".fe-block-xxx { ... }"
    text = re.sub(r'\.[a-zA-Z0-9_-]+\s*\{[^}]*\}', '', text)

    # Strip <style>...</style> blocks
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.I)

    # Strip remaining HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)

    # Collapse excessive whitespace / blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = text.strip()

    if max_len:
        text = text[:max_len]

    return text

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
# Patterns are checked case-insensitively against title + first 600 chars of description.
# Order matters for overlapping genres (more specific first).

GENRE_KEYWORDS = {
    # ── Electronic sub-genres (specific first) ───────────────────────────────
    'Liquid Drum & Bass':     [r'\bliquid\b.{0,20}\bdrum.?(?:and|&|n).?bass\b',
                               r'\bliquid\b.{0,20}\bdnb\b', r'\bliquid dnb\b'],
    'Halftime Drum & Bass':   [r'\bhalftime\b.{0,20}\bdrum.?(?:and|&|n).?bass\b',
                               r'\bhalftime\b.{0,20}\bdnb\b'],
    'Neurofunk Drum & Bass':  [r'\bneurofunk\b'],
    'Drum and Bass':          [r'\bdrum.?(?:and|&|n).?bass\b', r'\bD&B\b', r'\bDnB\b',
                               r'\bdnb\b(?!.*dubstep)'],
    'Melodic Dubstep':        [r'\bmelodic.{0,10}dubstep\b'],
    'Liquid Dubstep':         [r'\bliquid.{0,10}dubstep\b'],
    'Crunchy Dubstep':        [r'\bcrunchy.{0,10}dubstep\b'],
    'Riddim':                 [r'\briddim\b'],
    'Dubstep':                [r'\bdubstep\b'],
    'Future Bass':            [r'\bfuture\b.{0,10}\bbass\b(?! house)'],
    'Bass House':             [r'\bbass\b.{0,10}\bhouse\b'],
    'Bass Music':             [r'\bbass\b.{0,10}\bmusic\b', r'\bbassline\b'],
    'Footwork':               [r'\bfootwork\b', r'\bjuke\b'],
    'Psytrance':              [r'\bpsytrance\b', r'\bpsy.?trance\b',
                               r'\bpsychedelic\b.{0,10}\btrance\b'],
    'Prog Psy':               [r'\bprog(?:ressive)?.{0,10}psy\b'],
    'Trance':                 [r'\btrance\b(?!.*psy)'],
    'Hard Techno':            [r'\bhard.{0,10}techno\b'],
    'Melodic Techno':         [r'\bmelodic.{0,10}techno\b'],
    'Big Room Techno':        [r'\bbig.?room.{0,10}techno\b'],
    'Techno':                 [r'\btechno\b'],
    'Progressive House':      [r'\bprog(?:ressive)?.{0,10}house\b'],
    'Deep House':             [r'\bdeep.{0,10}house\b'],
    'Tech House':             [r'\btech.{0,10}house\b'],
    'Funky House':            [r'\bfunky.{0,10}house\b'],
    'Jackin House':           [r'\bjackin.{0,10}house\b'],
    'Hard House':             [r'\bhard.{0,10}house\b'],
    'Dark House':             [r'\bdark.{0,10}house\b'],
    'Tribal House':           [r'\btribal.{0,10}house\b'],
    'Big Room House':         [r'\bbig.?room.{0,10}house\b'],
    'House':                  [r'\bhouse\b.{0,20}\b(?:music|night|set|party|dj)\b',
                               r'\bhouse music\b'],
    'Electro House':          [r'\belectro.{0,10}house\b'],
    'Indie Dance':            [r'\bindie.{0,10}dance\b'],
    'UK Garage':              [r'\buk\b.{0,10}\bgarage\b', r'\bu\.?k\.?\s*garage\b',
                               r'\b2.?step\b'],
    'Jungle':                 [r'\bjungle\b.{0,20}\b(?:music|rave|night|dnb)\b'],
    'Breaks':                 [r'\bbreaks\b', r'\bbreakbeat\b'],
    'Ghettotech':             [r'\bghettotech\b'],
    'Livetronica':            [r'\blivetronica\b'],
    'Live Looping':           [r'\blive\b.{0,10}\blooping\b'],
    'Halftime':               [r'\bhalftime\b(?!.{0,20}drum)'],
    'Big Room':               [r'\bbig.?room\b(?!.{0,20}(?:techno|house))'],
    'Donk':                   [r'\bdonk\b'],
    'Folktronic':             [r'\bfolktronic\b'],
    'Future Beats':           [r'\bfuture.{0,10}beats\b'],
    'Future Funk':            [r'\bfuture.{0,10}funk\b'],
    'Electronic':             [r'\belectronic\b.{0,20}\b(?:music|set|night|dance|acts?)\b',
                               r'\bEDM\b', r'\bDJ\b.{0,20}\bset\b', r'\brave\b',
                               r'\bclub\b.{0,20}\bnight\b'],
    # ── Other genres ─────────────────────────────────────────────────────────
    'Ambient':       [r'\bambient\b'],
    'Downtempo':     [r'\bdowntempo\b'],
    'Trip-Hop':      [r'\btrip.?hop\b'],
    'Synthwave':     [r'\bsynthwave\b', r'\bsynth.?wave\b', r'\bretrowave\b'],
    'Synthpop':      [r'\bsynthpop\b', r'\bsynth.?pop\b'],
    'Electropop':    [r'\belectropop\b', r'\belectro.?pop\b'],
    'Electrofunk':   [r'\belectrofunk\b', r'\belectro.?funk\b'],
    'Electro':       [r'\belectro\b(?!.{0,5}(?:pop|funk|house|punk))'],
    'Dark Wave':     [r'\bdark.?wave\b', r'\bgothic\b.{0,20}\brock\b'],
    'Industrial':    [r'\bindustrial\b.{0,20}\b(?:music|rock|noise|band)\b'],
    'Experimental':  [r'\bexperimental\b', r'\bavant.?garde\b'],
    'Funk':          [r'\bfunk\b(?!.{0,5}(?:y|ier|iest))'],
    'Disco':         [r'\bdisco\b'],
    'Trap':          [r'\btrap\b.{0,20}\b(?:music|set|night|dj)\b',
                      r'\btrap\b(?=\s*[,/+&]|\s+(?:and|&)\s)',  # "trap and bass" etc
                      r'\btrap\b$'],
    'Hip-Hop':       [r'\bhip.?hop\b', r'\brap\b.{0,20}\b(?:music|show|night|artist|battle)\b',
                      r'\brap\b(?=\s*[,/+&]|\s+(?:and|&)\s)'],
    'R&B':           [r'\bR&B\b', r'\brhythm.?and.?blues\b', r'\bsoul\b.{0,20}\bmusic\b'],
    'Jazz':          [r'\bjazz\b'],
    'Blues':         [r'\bblues\b'],
    'Folk':          [r'\bfolk\b', r'\bacoustic\b.{0,20}\b(?:music|set|show|night)\b',
                      r'\bbluegrass\b'],
    'Classical':     [r'\bclassical\b', r'\borchestra\b', r'\bsymphony\b',
                      r'\bopera\b', r'\bchamber\b.{0,20}\bmusic\b',
                      r'\brecital\b', r'\bchoir\b', r'\bchoral\b'],
    'Punk':          [r'\bpunk\b'],
    'Metal':         [r'\bmetal\b(?!.{0,5}(?:work|lic|s\b))', r'\bdoom\b.{0,20}\bband\b'],
    'Rock':          [r'\brock\b(?!.{0,10}(?:climbing|wall|garden|island|island|creek|point|springs|ford|ville|ton|burg|view|port|dale|field|lake|wood|land|side))',
                      r'\bgarage\b.{0,10}\bband\b', r'\bpsychedelic\b.{0,20}\brock\b'],
    'Indie':         [r'\bindie\b', r'\balternative\b.{0,20}\b(?:music|rock|band)\b'],
    'Reggae':        [r'\breggae\b', r'\bska\b', r'\bdub\b.{0,20}\b(?:music|set|night)\b'],
    'Pop':           [r'\bpop\b(?!.{0,5}(?:up|art|corn|ular|ulation|quiz))'],
    'World Music':   [r'\bworld\b.{0,10}\bmusic\b', r'\bafrobeat\b', r'\blatino\b',
                      r'\bflamenc[oa]\b', r'\bklezmer\b', r'\bceltic\b',
                      r'\bcumbia\b', r'\bsalsa\b', r'\bbossa nova\b'],
}


def detect_genres(title, description=''):
    """Return list of genre name strings matching the event text."""
    text = f'{title} {description[:600]}'
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

    Returns (changed: bool, out_of_area: bool).
    out_of_area=True means the event geocoded to outside the Portland metro
    bounding box — callers should reject/skip these events.
    """
    from events.models import Genre
    from events.geocode import geocode_location, reverse_geocode_neighborhood, is_in_pdx_area

    changed = False
    out_of_area = False

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
                if not is_in_pdx_area(lat, lng):
                    out_of_area = True
                    return changed, out_of_area  # bail early — don't save out-of-area coords
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

    # Genres (separate M2M — add detected genres even if event already has some)
    if event.category == 'music' and event.pk:
        genre_names = detect_genres(event.title, event.description)
        for name in genre_names:
            genre, _ = Genre.objects.get_or_create(name=name)
            event.genres.add(genre)

    return changed, out_of_area
