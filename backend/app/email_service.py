"""E-Mail-Versand einer Rechnung (PDF im Anhang) über SMTP (MailHog)."""
import smtplib
from email.message import EmailMessage

from . import config, models, pdf


def _send(invoice: models.Invoice, to_email: str, settings, subject: str, text: str):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = (settings.email if settings and settings.email else config.MAIL_FROM)
    msg["To"] = to_email
    msg.set_content(text)
    msg.add_attachment(
        pdf.invoice_pdf(invoice, settings),
        maintype="application", subtype="pdf",
        filename=f"{invoice.number}.pdf",
    )
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as smtp:
        smtp.send_message(msg)


def send_invoice_email(invoice: models.Invoice, to_email: str, settings=None) -> None:
    sender = settings.company_name if settings and settings.company_name else "Ihr Rechnungssteller"
    text = (
        f"Guten Tag {invoice.customer_name},\n\n"
        f"anbei erhalten Sie die Rechnung {invoice.number} "
        f"über {invoice.total:.2f} EUR.\n\n"
        f"Mit freundlichen Grüßen\n{sender}"
    )
    _send(invoice, to_email, settings, f"Rechnung {invoice.number}", text)


def send_reminder_email(invoice: models.Invoice, to_email: str, settings=None) -> None:
    """Zahlungserinnerung / Mahnung für eine überfällige Rechnung."""
    sender = settings.company_name if settings and settings.company_name else "Ihr Rechnungssteller"
    due = invoice.due_date.strftime("%d/%m/%Y") if invoice.due_date else "—"
    text = (
        f"Guten Tag {invoice.customer_name},\n\n"
        f"unsere Rechnung {invoice.number} über {invoice.total:.2f} EUR war am "
        f"{due} fällig und ist nach unseren Unterlagen noch offen "
        f"(offener Betrag: {invoice.remaining:.2f} EUR).\n\n"
        f"Wir bitten Sie, den Betrag zeitnah zu begleichen. Sollte sich Ihre "
        f"Zahlung mit dieser E-Mail überschnitten haben, betrachten Sie diese "
        f"Erinnerung bitte als gegenstandslos.\n\n"
        f"Mit freundlichen Grüßen\n{sender}"
    )
    _send(invoice, to_email, settings,
          f"Zahlungserinnerung zu Rechnung {invoice.number}", text)
