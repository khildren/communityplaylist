"""
setup_instagram_session — log in to Instagram once and save a session file.

The session file is reused by harvest_instagram so credentials never need
to be stored in settings or env vars.

Run interactively (it will prompt for username + password):
    python manage.py setup_instagram_session

Or non-interactively (e.g. from a script):
    python manage.py setup_instagram_session --username myaccount --password mypass

The session is saved to IG_SESSION_DIR/session (default: /app/data/ig_session/session).
Set the IG_SESSION_DIR env var to change the path.

Tips:
  - Use a throwaway / secondary account, not your personal one.
  - If Instagram asks for a verification code, complete it in a real browser first,
    then re-run this command — the session will be valid for weeks to months.
  - To check whether the current session is still valid:
        python manage.py setup_instagram_session --check
"""
import os
import getpass
import instaloader
from django.core.management.base import BaseCommand

def _session_dir():
    from django.conf import settings
    return os.environ.get('IG_SESSION_DIR', os.path.join(str(settings.MEDIA_ROOT), '.ig_session'))

SESSION_DIR  = _session_dir()
SESSION_FILE = os.path.join(SESSION_DIR, 'session')


class Command(BaseCommand):
    help = 'Log in to Instagram and save a session file for harvest_instagram.'

    def add_arguments(self, parser):
        parser.add_argument('--username', type=str, default='',
                            help='Instagram username (prompted if omitted)')
        parser.add_argument('--password', type=str, default='',
                            help='Instagram password (prompted if omitted)')
        parser.add_argument('--check', action='store_true',
                            help='Test the existing session without re-logging in')

    def handle(self, *args, **options):
        os.makedirs(SESSION_DIR, exist_ok=True)

        L = instaloader.Instaloader(quiet=True)

        if options['check']:
            if not os.path.exists(SESSION_FILE):
                self.stderr.write('No session file found.')
                return
            try:
                L.load_session_from_file(username=None, filename=SESSION_FILE)
                # Try a lightweight call to verify the session is alive
                profile = instaloader.Profile.from_username(L.context, 'instagram')
                self.stdout.write(
                    self.style.SUCCESS(f'Session valid. Logged in as: {L.context.username}')
                )
            except Exception as e:
                self.stderr.write(f'Session invalid or expired: {e}')
            return

        username = options['username'] or input('Instagram username: ').strip()
        password = options['password'] or getpass.getpass('Instagram password: ')

        if not username or not password:
            self.stderr.write('Username and password are required.')
            return

        self.stdout.write(f'Logging in as @{username}…')
        try:
            L.login(username, password)
            L.save_session_to_file(SESSION_FILE)
            self.stdout.write(
                self.style.SUCCESS(
                    f'Session saved to {SESSION_FILE}\n'
                    f'harvest_instagram will use this automatically.'
                )
            )
        except instaloader.exceptions.BadCredentialsException:
            self.stderr.write('Bad username or password.')
        except instaloader.exceptions.TwoFactorAuthRequiredException:
            code = input('Two-factor code: ').strip()
            try:
                L.two_factor_login(code)
                L.save_session_to_file(SESSION_FILE)
                self.stdout.write(self.style.SUCCESS(f'Session saved to {SESSION_FILE}'))
            except Exception as e:
                self.stderr.write(f'2FA failed: {e}')
        except Exception as e:
            self.stderr.write(f'Login failed: {e}')
