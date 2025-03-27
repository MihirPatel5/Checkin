import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dataclasses import dataclass, field


@dataclass
class Email:
    """This is Email sending Service used for sending mail and attach all context of mail like text, file, links etc and add recipient, cc."""

    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = "abd.bodara@gmail.com"
    smtp_password: str = "rpkpcrezdnthnffg"

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
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)

            # Create email message
            msg = MIMEMultipart()
            msg["From"] = self.smtp_user
            msg["To"] = ", ".join(self._to)
            msg["Subject"] = self.subject

            if self._cc:
                msg["Cc"] = ", ".join(self._cc)

            # Add email content
            if self.text:
                msg.attach(MIMEText(self.text, "plain"))
            if self.html:
                msg.attach(MIMEText(self.html, "html"))

            # Send email
            server.sendmail(self.smtp_user, self._to + self._cc, msg.as_string())
            server.quit()

            return {"message": "Email sent successfully"}

        except Exception as e:
            raise ValueError(f"SMTP Error: {str(e)}")