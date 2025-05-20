require('dotenv').config();
const express = require('express');
const VoiceResponse = require('twilio').twiml.VoiceResponse;
const WebSocket = require('websocket').server;
const http = require('http');
const { Twilio } = require('twilio');
const speech = require('@google-cloud/speech');
const fs = require('fs');
const path = require('path');
const wav = require('wav');
const flags = require('cli-flag-parser');

let inInitialCall = true;
let finishedInitialCall = false;
let inVerificationCall = false;
let finishedVerificationCall = false;
let inPostVerification = false;
let finishedPostVerification = false;
let inPhoneTree = false;
let inPhoneTreeTime = Date.now();
let finishedPhoneTree = false;
let lastTranscript = '';

// Handle CLI flags
// Registering flags
flags
    .registerFlag(
        'redirect', 'Phone number to redirect to',
        process.env.REDIRECT_PHONE_NUMBER)
    .registerFlag(
        'twilio', 'Twilio phone number to call from',
        process.env.TWILIO_PHONE_NUMBER)
    .registerFlag('port', 'Port to run the server on', 3000)
    .registerFlag('host', 'Host to run the server on', process.env.HOST)

// Parsing the command-line arguments
const parsedFlags = flags.parse();
const REDIRECT_PHONE_NUMBER = parsedFlags.redirect;
const TWILIO_PHONE_NUMBER = parsedFlags.twilio;
const PORT = parsedFlags.port;
const HOST_URL = parsedFlags.host;

console.log(`Using redirect phone number: ${REDIRECT_PHONE_NUMBER}`);
console.log(`Using Twilio phone number: ${TWILIO_PHONE_NUMBER}`);


function convertRawToWav(inputPath, outputPath) {
    const reader = fs.createReadStream(inputPath);
    const writer = new wav.FileWriter(outputPath, {
        channels: 1,
        sampleRate: 8000,
        bitDepth: 8, // mu-law uses 8-bit encoding
        signed: true, // signed samples
        format: 7 // format 7 is mu-law in WAV spec
    });

    reader.pipe(writer);

    return new Promise((resolve, reject) => {
        writer.on('finish', () => {
            // console.log(`Converted to WAV: ${outputPath}`);
            // remove the raw file after conversion
            fs.unlink(inputPath, (err) => {
                if (err) {
                    console.error('Error deleting raw file:', err);
                }
            });
            resolve(outputPath);
        });
        writer.on('error', (err) => {
            console.error('Error converting to WAV:', err);
            reject(err);
        });
    });
}

// Initialize Twilio client
const client =
    new Twilio(process.env.TWILIO_ACCOUNT_SID, process.env.TWILIO_AUTH_TOKEN);

// Initialize Google Speech client
const speechClient = new speech.SpeechClient();

// Initialize Express app
const app = express();
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Create HTTP server
const server = http.createServer(app);
server.listen(parseInt(PORT), () => {
    console.log('Server is running on port ' + PORT);
    console.log(`To make a call, use: curl http://localhost:${PORT}/call`);
});

// Create WebSocket server
const wsServer =
    new WebSocket({ httpServer: server, autoAcceptConnections: false });

// Store active call SIDs
const activeCallSids = {};


// Handle WebSocket connections
wsServer.on('request', (request) => {
    const connection = request.accept(null, request.origin);
    let streamSid = null;
    let audioFileStream = null;
    let recognizeStream = null;
    let callSid = null;


    console.log('WebSocket connection accepted');

    connection.on('message', (message) => {
        if (message.type === 'utf8') {
            try {
                const data = JSON.parse(message.utf8Data);
                const eventType = data.event;

                if (eventType === 'start') {
                    if (finishedInitialCall && !finishedVerificationCall) {
                        inVerificationCall = true;
                    } else if (finishedVerificationCall && !finishedPostVerification) {
                        inPostVerification = true;
                    } else if (finishedPostVerification && !finishedPhoneTree) {
                        inPhoneTree = true;
                        inPhoneTreeTime = Date.now();
                    }

                    streamSid = data.streamSid;
                    // Extract and store the callSid from the streamSid context
                    callSid = data.callSid || (data.start && data.start.callSid);
                    if (callSid) {
                        activeCallSids[streamSid] = callSid;
                        console.log(`Associated Stream SID: ${streamSid} with Call SID: ${callSid}`);
                    } else {
                        console.error('No Call SID found in stream event');
                    }

                    // Create file and stream only after receiving streamSid
                    const recordingPath = path.join(__dirname, 'recordings');
                    if (!fs.existsSync(recordingPath)) {
                        fs.mkdirSync(recordingPath);
                    }

                    const audioFilePath = path.join(recordingPath, `${streamSid}.raw`);
                    audioFileStream = fs.createWriteStream(audioFilePath);

                    // Initialize recognition stream
                    recognizeStream =
                        speechClient
                            .streamingRecognize({
                                config: {
                                    encoding: 'MULAW',
                                    sampleRateHertz: 8000,
                                    languageCode: 'en-US',
                                    enableAutomaticPunctuation: true,
                                    model: 'phone_call',
                                    useEnhanced: true,
                                    interimResults: true,
                                    enableWordTimeOffsets: true,
                                    enableSpeakerDiarization: true,
                                    diarizationSpeakerCount: 2, // optional

                                    // Boost number recognition
                                    speechContexts: [{
                                        phrases: [
                                            'zero',
                                            'one',
                                            'two',
                                            'three',
                                            'four',
                                            'five',
                                            'six',
                                            'seven',
                                            'eight',
                                            'nine',
                                        ],
                                        boost: 20.0 // strong boost for number terms
                                    }],
                                    maxAlternatives: 1,
                                    enableVoiceActivityEvents: true
                                },
                                interimResults: true
                            })
                            .on('error',
                                (error) => {
                                    console.error('Speech recognition error:', error);
                                })
                            .on('data', (data) => {
                                const result = data.results[0];
                                if (result && result.alternatives[0]) {
                                    const transcript = result.alternatives[0].transcript;
                                    const isFinal = result.isFinal;
                                    const confidence = result.alternatives[0].confidence || 0;
                                    const inStage = inPhoneTree ? 'phoneTree' :
                                        inPostVerification ? 'postverify' :
                                            inVerificationCall ? 'verification' :
                                                inInitialCall ? 'initial' :
                                                    'unknown';
                                    if (transcript !== lastTranscript) {
                                        console.log(`[vtt/${inStage}] ${isFinal ? 'FINAL' : ''}: ${transcript}`);
                                        lastTranscript = transcript;
                                    }

                                    if (inInitialCall && !finishedInitialCall) {
                                        if (transcript.toUpperCase().includes('CALIFORNIA')) {
                                            finishedInitialCall = true;
                                            console.log('Finished initial call');
                                            const associatedCallSid = activeCallSids[streamSid];
                                            if (associatedCallSid) {
                                                client.calls(associatedCallSid)
                                                    .update({
                                                        url: `https://${HOST_URL}/dtmf?digits=1`,
                                                        method: 'POST'
                                                    })
                                                    .then(
                                                        () => console.log(
                                                            'Redirected to /dtmf to send DTMF'))
                                                    .catch(
                                                        err => console.error(
                                                            'Failed to redirect:', err));
                                            }
                                            return;
                                        }
                                    } else if (
                                        inVerificationCall && !finishedVerificationCall &&
                                        isFinal) {
                                        finishedVerificationCall = true;
                                        console.log(`Transcript: ${transcript}`);
                                        // check if the word "verification" is in the transcript
                                        if (!transcript.toLowerCase().includes(
                                            'verification')) {
                                            console.error(
                                                'No verification code detected. Exiting...');
                                            const associatedCallSid = activeCallSids[streamSid];
                                            if (associatedCallSid) {
                                                client.calls(associatedCallSid)
                                                    .update({
                                                        url: `https://${HOST_URL}/hangup`,
                                                        method: 'POST'
                                                    })
                                                    .then(() => console.log('Call hung up'))
                                                    .catch(
                                                        err => console.error(
                                                            'Failed to hang up:', err));
                                            }
                                        }

                                        // split on transcription and take the last part
                                        const lastPart = transcript.split('verification').pop();
                                        console.log(`Last part of transcript: ${lastPart}`);

                                        // get all numbers in the transcript
                                        const numbers = lastPart.match(/\d+/g);
                                        console.log(`Numbers found in transcript: ${numbers}`);
                                        // if number of numbers is not 4, exit everything
                                        if (!numbers || (numbers && numbers.length !== 4)) {
                                            console.error(
                                                'Invalid verification code detected. Expected 4 numbers.');
                                            const associatedCallSid = activeCallSids[streamSid];
                                            if (associatedCallSid) {
                                                client.calls(associatedCallSid)
                                                    .update({
                                                        url: `https://${HOST_URL}/hangup`,
                                                        method: 'POST'
                                                    })
                                                    .then(() => console.log('Call hung up'))
                                                    .catch(
                                                        err => console.error(
                                                            'Failed to hang up:', err));
                                            }
                                        }
                                        // send the numbers to the /dtmf endpoint
                                        const digits = numbers.join('W');
                                        console.log(`Detected verification code: ${digits}`);
                                        const associatedCallSid = activeCallSids[streamSid];
                                        if (associatedCallSid) {
                                            console.log(
                                                'Detected verification code – redirecting to /dtmf');
                                            client.calls(associatedCallSid)
                                                .update({
                                                    url: `https://${HOST_URL}/dtmf?digits=${digits}`,
                                                    method: 'POST'
                                                })
                                                .then(
                                                    () => console.log(
                                                        'Redirected to /dtmf to send DTMF'))
                                                .catch(
                                                    err => console.error(
                                                        'Failed to redirect:', err));
                                            return;
                                        }
                                    } else if (
                                        inPostVerification && !finishedPostVerification) {
                                        if (transcript.toUpperCase().includes('THANK YOU')) {
                                            finishedPostVerification = true;
                                            // time to navigate the phone tree
                                            const associatedCallSid = activeCallSids[streamSid];
                                            if (associatedCallSid) {
                                                client.calls(associatedCallSid)
                                                    .update({
                                                        url: `https://${HOST_URL}/dtmf?digits=WW3WWWWW1WWWWW5WWWWW0`,
                                                        method: 'POST'
                                                    })
                                                    .then(
                                                        () => console.log(
                                                            'Redirected to /dtmf to send DTMF'))
                                                    .catch(
                                                        err => console.error(
                                                            'Failed to redirect:', err));
                                            }
                                            return;
                                        }
                                    } else if (
                                        inPhoneTree && !finishedPhoneTree &&
                                        (Date.now() - inPhoneTreeTime) > 3000) {
                                        if (transcript.toUpperCase().includes(
                                            'STAY ON THE LINE')) {
                                            finishedPhoneTree = true;
                                            console.log(
                                                'redirecting to ' +
                                                process.env.REDIRECT_PHONE_NUMBER);
                                            const associatedCallSid = activeCallSids[streamSid];
                                            if (associatedCallSid) {
                                                console.log(
                                                    'redirecting to ' +
                                                    process.env.REDIRECT_PHONE_NUMBER);
                                                client.calls(associatedCallSid)
                                                    .update({
                                                        url: `https://${HOST_URL}/redirect`,
                                                        method: 'POST'
                                                    })
                                                    .then(
                                                        () => console.log(
                                                            'Call redirected to ' +
                                                            process.env.REDIRECT_PHONE_NUMBER))
                                                    .catch(
                                                        err => console.error(
                                                            'Failed to redirect:', err));
                                            }
                                        } else if (transcript.toUpperCase().includes(
                                            'MAXIMUM NUMBER')) {
                                            finishedPhoneTree = true;
                                            // hangup
                                            const associatedCallSid = activeCallSids[streamSid];
                                            if (associatedCallSid) {
                                                client.calls(associatedCallSid)
                                                    .update({
                                                        url: `https://${HOST_URL}/hangup`,
                                                        method: 'POST'
                                                    })
                                                    .then(() => console.log('Call hung up'))
                                                    .catch(
                                                        err => console.error(
                                                            'Failed to hang up:', err));
                                            }
                                        }
                                    }
                                }
                            });
                }

                if (eventType === 'media' && data.media && data.media.payload) {
                    const audioData = Buffer.from(data.media.payload, 'base64');
                    if (audioFileStream) audioFileStream.write(audioData);
                    if (recognizeStream) recognizeStream.write(audioData);
                }

                if (eventType === 'stop') {
                    if (recognizeStream) recognizeStream.end();
                    if (audioFileStream) {
                        audioFileStream.end(() => {
                            console.log(`Saved recording for stream: ${streamSid}`);
                            const wavPath =
                                path.join(__dirname, 'recordings', `${streamSid}.wav`);
                            convertRawToWav(
                                path.join(__dirname, 'recordings', `${streamSid}.raw`),
                                wavPath);
                        });
                    }
                    // Clean up when the stream ends
                    if (streamSid && activeCallSids[streamSid]) {
                        delete activeCallSids[streamSid];
                    }
                }
            } catch (err) {
                console.error('Failed to parse WebSocket message:', err);
            }
        }
    });

    connection.on('close', () => {
        // console.log(`WebSocket connection closed for Stream SID: ${streamSid ||
        // '(unknown)'}`);
        if (recognizeStream) recognizeStream.end();
        if (audioFileStream && !audioFileStream.closed) audioFileStream.end();
        // Clean up when the connection closes
        if (streamSid && activeCallSids[streamSid]) {
            delete activeCallSids[streamSid];
        }
    });
});

// Endpoint to initiate a call
app.get('/call', async (req, res) => {
    try {
        const numberCalling = [process.env.TWILIO_PHONE_NUMBER,
        process.env.TWILIO_PHONE_NUMBER2
        ][Math.floor(Math.random() * 2)];
        console.log(
            `Calling from ${numberCalling} to ${process.env.TO_PHONE_NUMBER}`);
        const call = await client.calls.create({
            url: `https://${HOST_URL}/twiml`,
            to: process.env.TO_PHONE_NUMBER,
            from: numberCalling
        });

        console.log(`Call initiated with SID: ${call.sid}`);
        res.json({ success: true, callSid: call.sid });
    } catch (error) {
        console.error('Error initiating call:', error);
        res.status(500).json({ success: false, error: error.message });
    }
});


// Redirect TwiML endpoint
app.post('/redirect', (req, res) => {
    const twiml = new VoiceResponse();

    // Replace with your env variable
    twiml.dial(process.env.REDIRECT_PHONE_NUMBER);

    console.log('Redirecting call to REDIRECT_PHONE_NUMBER');

    res.type('text/xml');
    res.send(twiml.toString());

    setTimeout(() => {
        process.exit(0);
    }, 10000);
});

app.post('/hangup', (req, res) => {
    const twiml = new VoiceResponse();

    // Replace with your env variable
    twiml.hangup();

    console.log('Hanging up call');

    res.type('text/xml');
    res.send(twiml.toString());

    setTimeout(() => {
        process.exit(1);
    }, 3000);
});

// Endpoint to serve TwiML instructions
app.post('/twiml', (req, res) => {
    console.log('Received TwiML request');
    const twiml = new VoiceResponse();

    // Connect the call to a media stream
    const start = twiml.start();
    start.stream({ url: `wss://${HOST_URL}/stream`, track: 'inbound' });

    // Say something to the person who answers
    twiml.say('Hi');

    // Pause to let them speak
    twiml.pause({ length: 30 });

    // End the call
    twiml.hangup();

    console.log('TwiML response generated');

    res.type('text/xml');
    res.send(twiml.toString());
});

app.post('/dtmf', (req, res) => {
    const digits = req.query.digits || '1'; // default to '1' if not specified
    console.log(`/dtmf digit ${digits}`);

    const twiml = new VoiceResponse();

    // Stop current media stream
    twiml.stop().stream();

    // Wait 1s, then play digits
    twiml.play({ digits: `${digits}` });

    // Resume media stream
    const start = twiml.start();
    start.stream({ url: `wss://${HOST_URL}/stream`, track: 'inbound' });

    twiml.pause({ length: 60 });

    res.type('text/xml');
    res.send(twiml.toString());
});

// Endpoint for Twilio to send stream events
app.post('/stream', (req, res) => {
    console.log('Stream status update:', req.body);

    // Store the callSid from stream events if available
    if (req.body && req.body.streamSid && req.body.callSid) {
        activeCallSids[req.body.streamSid] = req.body.callSid;
        console.log(`Stored Call SID ${req.body.callSid} for Stream SID ${req.body.streamSid} from stream event`);
    }

    res.sendStatus(200);
});


setTimeout(() => {
    const options = { hostname: 'localhost', port: PORT, path: '/call', method: 'GET' };

    const req = http.request(options, (res) => {
        console.log(`STATUS: ${res.statusCode}`);
        res.setEncoding('utf8');
        res.on('data', (chunk) => {
            console.log(`BODY: ${chunk}`);
        });
    });

    req.on('error', (e) => {
        console.error(`Request error: ${e.message}`);
    });

    req.end();
}, 1000);