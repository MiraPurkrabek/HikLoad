import os
import yaml
import logging
import logging.config

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger('EmailSender')

developer_email = "miroslav.purkrabek@skvflorbal.cz"

def _load_credentials():
    try:
        this_file_path = os.path.dirname(os.path.abspath(__file__))
        passwords_path = os.path.join(this_file_path, "..", "passwords", "email.yml")
        with open(passwords_path) as pass_file:
            email_passwords = yaml.safe_load(pass_file)
    except FileNotFoundError:
        email_passwords = None
    logger.debug("Passwords loaded")
    
    return email_passwords

def send_email(
    to,
    cc=None,
    subject="Test email from HikLoad",
    body="This is a test email sent automatically through Python",
    bcc=None, 
):
    # Set up the email details
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['To'] = to
    if cc is not None:
        msg['Cc'] = cc
    else:
        cc=""
    if bcc is not None:
        msg['Bcc'] = bcc
    else:
        bcc=""

    # Add signature
    body += "\n\n"
    body += "Best regards,\nYour SKV Robot"
    body += "\n(please, do not respond to this email, I cannot read)"

    # Add the email body
    msg.attach(MIMEText(body, 'plain'))
    
    passwords = _load_credentials()
    if passwords is None:
        logger.error("No passwords for email sending. Email not sent.")
        return

    username = "{:s}@{:s}".format(passwords['o365']['username'], passwords['o365']['server'])
    msg['From'] = username
    
    # Set up the SMTP server and authenticate
    smtp_server = "smtp.office365.com"
    smtp_port = 587  # Use port 587 for STARTTLS
    
    try:
        # Connect to the SMTP server
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()  # Upgrade the connection to a secure encrypted SSL/TLS connection
            server.login(username, passwords['o365']['password'])  # Authenticate with the server
            receivers = cc.split(",") + bcc.split(",") + to.split(",")
            server.sendmail(msg['From'], receivers, msg.as_string())  # Send the email
            logger.info("Email sent successfully!")
    except Exception as e:
        logger.warning("Failed to send email: {}".format(e))
        
    return


def send_report_email(to=None):
    this_file_path = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(this_file_path, "..", "logs", "latest.log")
    log_str = ""
    with open(log_path, "r") as f:
        log_str = f.read()
        
    if to is None:
        to = developer_email

    send_email(
        to=to,
        subject="[HikLoad] Crash report",
        body=log_str,
        bcc=developer_email
    )
    
def send_failure_email(to, body=None, video_name=None):
    
    if body is None:
        body = "SKV server crashed while downloading your video '{:s}'. Try to download it again. If the problem persists (you see this email for the second time), contact Mira Purkrabek about details.".format(video_name)
    
    send_email(
        to = to,
        bcc = developer_email,
        body = body,
        subject="[SKV Server] Video download failed"
    )
    
def send_success_email(to, body=None, video_name=None):
    
    if body is None:
        body = "Your video '{:s}' is ready. ".format(video_name)
        body += "Download it at: "
        body += "https://sokolvinohrady-my.sharepoint.com/:f:/g/personal/robot_skv_01_skvflorbal_cz/Emm_27OYqjpFjM0jF9sFQSkBDubvdEIq1TJKbAoNjgN8cA?e=ZWS2EV"
        body += "\nThe video will be available for 7 days. After that it will be automatically deleted."
    
    send_email(
        to = to,
        bcc = developer_email,
        body = body,
        subject="[SKV Server] Video ready"
    )


if __name__ == "__main__":
    config_path = "logging_config.yml"
    with open(config_path, "r") as f:
        config_data = yaml.safe_load(f.read())
        logging.config.dictConfig(config_data)
    logger = logging.getLogger("EmailSender")
    
    send_email(
        to=developer_email,
        cc="purkrabekspeedy@seznam.cz",
        bcc="mira.purkrabek@gmail.com",
    )