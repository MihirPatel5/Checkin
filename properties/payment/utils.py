import requests
from django.utils.translation import gettext as _

from utils.email_services import Email


def send_subscription_invoice_email(invoice, landlord):
    """Send subscription invoice email with PDF attachment"""
    try:
        # Download the PDF from Stripe
        response = requests.get(invoice.pdf_url)
        if response.status_code != 200:
            raise Exception(f"Failed to download PDF: {response.status_code}")
        
        pdf_content = response.content
        filename = f"Invoice_{invoice.stripe_invoice_id}.pdf"
        
        # Prepare email content
        subject = _("Your Subscription Invoice")
        text_content = _(
            f"Hello {landlord.first_name},\n\n"
            "Thank you for your subscription payment. "
            f"Your invoice #{invoice.stripe_invoice_id} for ${invoice.amount} is attached.\n\n"
            "You can also view it online: "
            f"{invoice.hosted_invoice_url}\n\n"
            "Best regards,\n"
            "The Billing Team"
        )
        
        html_content = f"""
        <html>
            <body>
                <p>Hello {landlord.first_name},</p>
                <p>Thank you for your subscription payment.</p>
                <p>Your invoice <strong>#{invoice.stripe_invoice_id}</strong> for <strong>${invoice.amount}</strong> is attached.</p>
                <p>You can also <a href="{invoice.hosted_invoice_url}">view it online</a>.</p>
                <p>Best regards,<br>The Billing Team</p>
            </body>
        </html>
        """
        
        # Create and send email
        email = Email(subject=subject)
        email.to(landlord.email)
        email.add_text(text_content)
        email.add_html(html_content)
        email.attach_file(pdf_content, filename)
        email.send()
        
        return True
    except Exception as e:
        # logger.error(f"Failed to send invoice email: {str(e)}")
        return False