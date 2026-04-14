"""
Custom SMTP email backend that skips TLS certificate verification.
Required because Plesk's localhost SMTP uses a self-signed certificate.
"""
import ssl
from django.core.mail.backends.smtp import EmailBackend


class LocalSMTPBackend(EmailBackend):
    def open(self):
        if self.connection:
            return False
        import smtplib
        connection_params = {
            'host': self.host,
            'port': self.port,
            'timeout': self.timeout,
        }
        try:
            self.connection = smtplib.SMTP(**connection_params)
            self.connection.ehlo()
            if self.use_tls:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                self.connection.starttls(context=ctx)
                self.connection.ehlo()
            if self.username and self.password:
                self.connection.login(self.username, self.password)
            return True
        except Exception:
            if not self.fail_silently:
                raise
