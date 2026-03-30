# Community Playlist — PDX Events Platform

Live at: https://communityplaylist.com

## Stack
- Django 6.0 on Ubuntu 24 / Plesk Obsidian
- Gunicorn + Apache/Nginx reverse proxy
- SQLite database
- Leaflet.js + CARTO dark map tiles

## Key Features
- Google Calendar iCal import with recurring event support
- 2131 MusicBrainz genres with autocomplete tagging
- OpenStreetMap geocoding for event venues
- Dark map view with clickable event pins
- Genre / neighborhood / date / free filters
- Event detail wiki pages with community photo upload
- Public event submission form
- Discord webhooks (admin-only + public events channel)
- Daily digest command

## Server
- IP: 66.175.239.235
- Webroot: /var/www/vhosts/communityplaylist.com/django/
- Venv: ./venv/
- Service: communityplaylist.service (gunicorn)

## Daily Commands
```bash
# Restart app
systemctl restart communityplaylist

# Import events from Google Calendar
python manage.py import_subduction

# Post todays events to Discord
python manage.py daily_digest

# Geocode events missing coordinates
python manage.py shell -c "from events.geocode import geocode_location..."
```

## Scheduled Tasks (Plesk)
- Hourly: import_subduction
- Daily 9am: daily_digest

## Discord Webhooks
- #admin-only: new submission alerts (views.py DISCORD_WEBHOOK)
- #events: approved event announcements (admin.py DISCORD_EVENTS)

## Git