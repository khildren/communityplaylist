/**
 * VenueFeed admin: tag-style autocomplete for default_genres (multi) and residents (multi).
 * Genres   → /genres/   (multi-tag)
 * Residents → /artists/ (multi-tag, MusicBrainz fallback, "Add locally" if nothing found)
 */
document.addEventListener('DOMContentLoaded', function () {

    // ── Show/hide music fields based on category ──────────────────────────────
    var catSelect    = document.getElementById('id_default_category');
    var genreRow     = document.querySelector('.field-default_genres');
    var residentsRow = document.querySelector('.field-residents');

    function toggleMusicFields() {
        var isMusic = catSelect && catSelect.value === 'music';
        if (genreRow)     genreRow.style.display     = isMusic ? '' : 'none';
        if (residentsRow) residentsRow.style.display  = isMusic ? '' : 'none';
    }
    if (catSelect) {
        toggleMusicFields();
        catSelect.addEventListener('change', toggleMusicFields);
    }

    // ── Shared tag-widget builder ─────────────────────────────────────────────
    function buildTagWidget(opts) {
        /**
         * opts.hiddenSelect  — native <select multiple> (hidden, drives form submit)
         * opts.endpoint      — autocomplete URL
         * opts.placeholder   — input placeholder
         * opts.allowCreate   — show "Add locally" when no results (artists only)
         * opts.createEndpoint — POST URL for creating new record
         */
        var sel = opts.hiddenSelect;
        if (!sel) return;

        // Hide native widget + any Django-added buttons
        var wrapper = sel.closest('.related-widget-wrapper') || sel.parentNode;
        wrapper.style.display = 'none';

        var container = document.createElement('div');
        container.style.cssText = 'margin-top:4px';

        var tagBar = document.createElement('div');
        tagBar.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px';

        var inputWrap = document.createElement('div');
        inputWrap.style.cssText = 'position:relative;max-width:420px';

        var input = document.createElement('input');
        input.type         = 'text';
        input.autocomplete = 'off';
        input.placeholder  = opts.placeholder;
        input.style.cssText = 'width:100%;padding:6px 10px;font-size:13px;border:1px solid #ccc;border-radius:4px;box-sizing:border-box';

        var dropdown = document.createElement('div');
        dropdown.style.cssText = 'position:absolute;top:100%;left:0;right:0;background:#fff;border:1px solid #ccc;border-top:none;border-radius:0 0 4px 4px;max-height:240px;overflow-y:auto;z-index:9999;display:none;box-shadow:0 4px 8px rgba(0,0,0,.15)';

        inputWrap.appendChild(input);
        inputWrap.appendChild(dropdown);
        container.appendChild(tagBar);
        container.appendChild(inputWrap);
        wrapper.parentNode.insertBefore(container, wrapper.nextSibling);

        var selected = [];

        // Pre-populate from existing select values
        Array.from(sel.options).forEach(function (opt) {
            if (opt.selected && opt.value) {
                addItem({ id: opt.value, name: opt.text }, true);
            }
        });

        function escHtml(s) {
            return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        }

        function renderTag(item) {
            var tag = document.createElement('span');
            tag.dataset.id = item.id;
            tag.style.cssText = 'display:inline-flex;align-items:center;gap:5px;background:#1a2a1a;color:#88dd88;border:1px solid #55aa55;padding:3px 10px;border-radius:20px;font-size:.85em';
            if (opts.allowCreate) {
                // artist tags — teal
                tag.style.background = '#1a2a3a';
                tag.style.color = '#66bbff';
                tag.style.borderColor = '#4499cc';
            }
            tag.innerHTML = escHtml(item.name) + ' <span style="cursor:pointer;font-size:14px;line-height:1" title="Remove">×</span>';
            tag.querySelector('span').addEventListener('click', function () {
                selected = selected.filter(function (s) { return s.id != item.id; });
                tag.remove();
                syncSelect();
            });
            tagBar.appendChild(tag);
        }

        function syncSelect() {
            Array.from(sel.options).forEach(function (o) {
                o.selected = !!selected.find(function (s) { return s.id == o.value; });
            });
        }

        function addItem(item, skipSync) {
            if (selected.find(function (s) { return s.id == item.id; })) return;
            selected.push(item);
            // Ensure option exists in hidden select
            if (!sel.querySelector('option[value="' + item.id + '"]')) {
                var o = document.createElement('option');
                o.value = item.id;
                o.text  = item.name;
                sel.appendChild(o);
            }
            if (!skipSync) syncSelect();
            renderTag(item);
            input.value = '';
            dropdown.style.display = 'none';
        }

        function dropdownRow(text, sublabel, onClick) {
            var row = document.createElement('div');
            row.style.cssText = 'padding:8px 12px;cursor:pointer;font-size:13px;color:#333;display:flex;align-items:center;justify-content:space-between';
            var span = document.createElement('span');
            span.textContent = text;
            row.appendChild(span);
            if (sublabel) {
                var badge = document.createElement('span');
                badge.textContent = sublabel;
                badge.style.cssText = 'font-size:.7em;color:#888;margin-left:8px';
                row.appendChild(badge);
            }
            row.addEventListener('mouseover', function () { row.style.background = '#f0f0f0'; });
            row.addEventListener('mouseout',  function () { row.style.background = ''; });
            row.addEventListener('mousedown', function (e) { e.preventDefault(); onClick(); });
            return row;
        }

        var debounce;
        input.addEventListener('input', function () {
            clearTimeout(debounce);
            var q = input.value.trim();
            if (q.length < 2) { dropdown.style.display = 'none'; return; }

            debounce = setTimeout(function () {
                fetch(opts.endpoint + '?q=' + encodeURIComponent(q))
                    .then(function (r) { return r.json(); })
                    .then(function (data) {
                        dropdown.innerHTML = '';

                        // Filter out already-selected
                        var filtered = data.filter(function (item) {
                            return !selected.find(function (s) { return s.id == item.id; });
                        });

                        filtered.forEach(function (item) {
                            var sublabel = item.mb_id ? 'MusicBrainz' : (item.local_only ? 'local' : '');
                            dropdown.appendChild(dropdownRow(item.name, sublabel, function () {
                                addItem({ id: item.id, name: item.name });
                                syncSelect();
                            }));
                        });

                        // "Add locally" option when nothing found and allowCreate=true
                        if (opts.allowCreate && filtered.length === 0) {
                            var addRow = dropdownRow('➕ Add "' + q + '" to local DB', 'new artist', function () {
                                fetch(opts.createEndpoint, {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCsrf()},
                                    body: JSON.stringify({ name: q }),
                                })
                                .then(function (r) { return r.json(); })
                                .then(function (artist) {
                                    addItem({ id: artist.id, name: artist.name });
                                    syncSelect();
                                });
                            });
                            addRow.style.color = '#4499cc';
                            dropdown.appendChild(addRow);
                        }

                        dropdown.style.display = dropdown.children.length ? 'block' : 'none';
                    });
            }, 220);
        });

        input.addEventListener('blur', function () {
            setTimeout(function () { dropdown.style.display = 'none'; }, 180);
        });
    }

    function getCsrf() {
        var el = document.querySelector('[name=csrfmiddlewaretoken]');
        return el ? el.value : '';
    }

    // ── Build widgets ─────────────────────────────────────────────────────────
    buildTagWidget({
        hiddenSelect   : document.getElementById('id_default_genres'),
        endpoint       : '/genres/',
        placeholder    : 'Search genres… (multi)',
        allowCreate    : false,
    });

    buildTagWidget({
        hiddenSelect   : document.getElementById('id_residents'),
        endpoint       : '/artists/',
        placeholder    : 'Search artists… (MusicBrainz if not found)',
        allowCreate    : true,
        createEndpoint : '/artists/add/',
    });
});
