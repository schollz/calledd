# Call EDD

This repository is a simple script that uses Twilio and Google's Speech-to-Text that can be used
to call EDD (Employment Development Department) in California and get through to talk to a human.
Usually works to get a human representative within about 30.

It costs about $1.50 to get the Twilio number and usually about $1-$5 to get through to a human, depending on how long you are on hold.
I automated this in a website, [calledd.com](https://calledd.com) where you can pay $15 to do all this automatically.


The script will call the EDD, wait for the automated system to ask for verification code, dial in the code,
and then wait for the system to transfer the call, and if so, it will redirect the call to your personal number.


## Pre-requisites


### Setup `.env` file

Create a `.env` file which has the following variables:

```env
HOST=
PORT=3000
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=
GOOGLE_APPLICATION_CREDENTIALS=speech-transcription.json
TO_PHONE_NUMBER=18772384373
REDIRECT_PHONE_NUMBER=
```

### Install and Configure ngrok

Visit [https://ngrok.com](https://ngrok.com), sign up for a free account, and
download the ngrok binary for your system. In your terminal, run:

```bash
ngrok config add-authtoken <your_authtoken>
```

To expose your local server on port 3000, run:

```bash
ngrok http 3000
```

Copy the generated HTTPS URL from the "Forwarding" section (e.g.,
`1234abcd.ngrok.io`) and set it as the `HOST` value in your `.env` file.

### Get Twilio Credentials + Phone Number

Log in to your [Twilio Console](https://www.twilio.com/console). Copy your
**Account SID** and **Auth Token** from the "Account Info" section. Then,
purchase a Twilio phone number that supports SMS and voice from the "Phone
Numbers" section. Paste these into your `.env` as `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, and `TWILIO_PHONE_NUMBER`.

### Set Up Google Speech-to-Text

Go to the [Google Cloud Console](https://console.cloud.google.com/), create or
select a project, and enable the **Speech-to-Text API**. Then navigate to **IAM
& Admin > Service Accounts**, create a new service account, and under **Keys**,
choose **Add Key > Create new key > JSON**. This will download a JSON
file—rename it to `speech-transcription.json` and set the
`GOOGLE_APPLICATION_CREDENTIALS` path accordingly.

### Set Redirect Number

Set `REDIRECT_PHONE_NUMBER` in your `.env` to the personal number you want
Twilio to redirect calls to.



## Usage

After setting up the environment, install the required dependencies:

```bash
npm install
```

Then, run the script:

```bash
node calledd.js
```

This will make a single call.

To run it continuously with a 2-minute timeout loop:

```bash
while true; do
  timeout 120 node calledd.js
  code=$?
  if [ "$code" -eq 0 ]; then
    break
  fi
done
```