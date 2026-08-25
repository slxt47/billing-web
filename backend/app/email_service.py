"""E-Mail-Versand (PDF im Anhang) über SMTP.

In der Entwicklung läuft das unauthentifiziert gegen MailHog. In Produktion
werden über SMTP_USER/SMTP_PASSWORD/SMTP_USE_TLS echte Zugangsdaten für einen
richtigen Mailversand verwendet (siehe scripts/setup-prod.sh)."""
import smtplib
from email.message import EmailMessage

from . import config, models, pdf


def _smtp_send(msg: EmailMessage) -> None:
    if config.SMTP_USE_TLS:
        smtp = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
        try:
            smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        finally:
            smtp.quit()
        return
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(msg)


def _attach_and_send(to_email: str, settings, subject: str, text: str,
                     attachment: bytes, filename: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = (settings.email if settings and settings.email else config.MAIL_FROM)
    msg["To"] = to_email
    msg.set_content(text)
    msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=filename)
    _smtp_send(msg)


def _send(invoice: models.Invoice, to_email: str, settings, subject: str, text: str):
    _attach_and_send(to_email, settings, subject, text,
                     pdf.invoice_pdf(invoice, settings), f"{invoice.number}.pdf")


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


def send_payment_confirmation(invoice: models.Invoice, to_email: str, settings=None) -> None:
    """Automatische Zahlungsbestätigung, sobald eine Rechnung vollständig
    bezahlt ist."""
    sender = settings.company_name if settings and settings.company_name else "Ihr Rechnungssteller"
    text = (
        f"Guten Tag {invoice.customer_name},\n\n"
        f"vielen Dank! Wir bestätigen den vollständigen Zahlungseingang zu "
        f"Rechnung {invoice.number} über {invoice.total:.2f} EUR.\n\n"
        f"Mit freundlichen Grüßen\n{sender}"
    )
    _send(invoice, to_email, settings,
          f"Zahlungsbestätigung zu Rechnung {invoice.number}", text)


def send_quote_email(quote: models.Quote, to_email: str, settings=None) -> None:
    sender = settings.company_name if settings and settings.company_name else "Ihr Rechnungssteller"
    text = (
        f"Guten Tag {quote.customer_name},\n\n"
        f"anbei erhalten Sie unser Angebot {quote.number} "
        f"über {quote.total:.2f} EUR.\n\n"
        f"Mit freundlichen Grüßen\n{sender}"
    )
    _attach_and_send(to_email, settings, f"Angebot {quote.number}", text,
                     pdf.quote_pdf(quote, settings), f"{quote.number}.pdf")


def send_delivery_note_email(delivery_note: models.DeliveryNote, to_email: str, settings=None) -> None:
    sender = settings.company_name if settings and settings.company_name else "Ihr Rechnungssteller"
    text = (
        f"Guten Tag {delivery_note.customer_name},\n\n"
        f"anbei erhalten Sie den Lieferschein {delivery_note.number}.\n\n"
        f"Mit freundlichen Grüßen\n{sender}"
    )
    _attach_and_send(to_email, settings, f"Lieferschein {delivery_note.number}", text,
                     pdf.delivery_note_pdf(delivery_note, settings), f"{delivery_note.number}.pdf")
