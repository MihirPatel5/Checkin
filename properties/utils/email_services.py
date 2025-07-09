from email import encoders
from email.mime.base import MIMEBase
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dataclasses import dataclass, field
from django.conf import settings

@dataclass
class Email:
    """This is Email sending Service used for sending mail and attach all context of mail like text, file, links etc and add recipient, cc."""

    smtp_server: str = settings.EMAIL_HOST
    smtp_port: int = settings.EMAIL_PORT
    smtp_user: str = settings.EMAIL_HOST_USER
    smtp_password: str = settings.EMAIL_HOST_PASSWORD

    _to: list = field(default_factory=list)
    _cc: list = field(default_factory=list)
    subject: str | None = None
    html: str | None = None
    text: str | None = None

    def __init__(self, subject: str):
        self.subject = subject
        self._to = []
        self._cc = []

    def to(self, email: str, name: str | None = None):
        recipient = f"{name} <{email}>" if name else email
        self._to.append(recipient)
        return self

    def cc(self, cc: str):
        if cc:
            self._cc.append(cc)
        return self

    def add_text(self, text: str):
        self.text = text
        return self

    def add_html(self, html: str):
        self.html = html
        return self

    def attach_file(self, file_data, filename, mimetype='application/pdf'):
        """Attach a file to the email"""
        if not hasattr(self, '_attachments'):
            self._attachments = []
        self._attachments.append({
            'data': file_data,
            'filename': filename,
            'mimetype': mimetype
        })
        return self

    def validate(self):
        if not self._to:
            raise ValueError("Recipient email is required")
        if not self.subject:
            raise ValueError("Email subject is required")
        if not self.text and not self.html:
            raise ValueError("Either an 'html' or 'text' must be provided")

    def send(self):
        self.validate()

        try:
            # Set up the email server
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.smtp_user, self.smtp_password)

            # Create email message
            msg = MIMEMultipart()
            msg["From"] = settings.DEFAULT_FROM_EMAIL
            msg["To"] = ", ".join(self._to)
            msg["Subject"] = self.subject

            if self._cc:
                msg["Cc"] = ", ".join(self._cc)

            # Add email content
            if self.text:
                msg.attach(MIMEText(self.text, "plain"))
            if self.html:
                msg.attach(MIMEText(self.html, "html"))
            if hasattr(self, '_attachments'):
                for attachment in self._attachments:
                    part = MIMEBase(*attachment['mimetype'].split('/'))
                    part.set_payload(attachment['data'])
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename="{attachment["filename"]}"'
                    )
                    msg.attach(part)
            # Send email
            server.sendmail(self.smtp_user, self._to + self._cc, msg.as_string())
            server.quit()

            return {"message": "Email sent successfully"}

        except Exception as e:
            raise ValueError(f"SMTP Error: {str(e)}")
