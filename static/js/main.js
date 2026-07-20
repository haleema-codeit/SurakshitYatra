document.addEventListener('DOMContentLoaded', () => {
    const body = document.body;
    const statusTitle = document.getElementById('status-title');
    const statusDesc = document.getElementById('status-desc');
    const statusIcon = document.getElementById('status-icon');
    const drowsyOverlay = document.getElementById('drowsy-overlay');
    const alarmSound = document.getElementById('alarm-sound');
    const clockElement = document.getElementById('clock');
    const startOverlay = document.getElementById('start-overlay');
    const startBtn = document.getElementById('start-btn');
    if (alarmSound) {
        alarmSound.volume = 1.0;
    }

    let monitoringActive = false;
    let lastVoiceAlert = 0;
    const VOICE_COOLDOWN = 4000;
    let isDanger = false;
    let lastDangerAlarmTime = 0;

    // Layered Web Audio siren for danger states.
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const playDangerSiren = () => {
        const now = Date.now();
        if (now - lastDangerAlarmTime < 650) return;
        lastDangerAlarmTime = now;

        const t = audioCtx.currentTime;
        const master = audioCtx.createGain();
        const compressor = audioCtx.createDynamicsCompressor();
        compressor.threshold.setValueAtTime(-24, t);
        compressor.knee.setValueAtTime(18, t);
        compressor.ratio.setValueAtTime(16, t);
        compressor.attack.setValueAtTime(0.003, t);
        compressor.release.setValueAtTime(0.16, t);
        master.connect(compressor);
        compressor.connect(audioCtx.destination);

        master.gain.setValueAtTime(0.0001, t);
        master.gain.exponentialRampToValueAtTime(0.95, t + 0.03);
        master.gain.setValueAtTime(0.95, t + 0.82);
        master.gain.exponentialRampToValueAtTime(0.0001, t + 1.05);

        const sirenHigh = audioCtx.createOscillator();
        sirenHigh.type = 'sawtooth';
        sirenHigh.frequency.setValueAtTime(620, t);
        sirenHigh.frequency.linearRampToValueAtTime(1420, t + 0.22);
        sirenHigh.frequency.linearRampToValueAtTime(700, t + 0.46);
        sirenHigh.frequency.linearRampToValueAtTime(1550, t + 0.72);
        sirenHigh.frequency.linearRampToValueAtTime(780, t + 1.02);

        const sirenBite = audioCtx.createOscillator();
        sirenBite.type = 'square';
        sirenBite.frequency.setValueAtTime(930, t);
        sirenBite.frequency.linearRampToValueAtTime(1720, t + 0.2);
        sirenBite.frequency.linearRampToValueAtTime(980, t + 0.45);
        sirenBite.frequency.linearRampToValueAtTime(1840, t + 0.72);
        sirenBite.frequency.linearRampToValueAtTime(1040, t + 1.02);

        const lowBuzz = audioCtx.createOscillator();
        lowBuzz.type = 'square';
        lowBuzz.frequency.setValueAtTime(92, t);

        sirenHigh.connect(master);
        sirenBite.connect(master);
        lowBuzz.connect(master);
        sirenHigh.start(t);
        sirenBite.start(t);
        lowBuzz.start(t);
        sirenHigh.stop(t + 1.05);
        sirenBite.stop(t + 1.05);
        lowBuzz.stop(t + 1.05);
    };

    const playDangerAlarm = () => {
        if (audioCtx.state === 'suspended') audioCtx.resume();
        playDangerSiren();
        if (!alarmSound) return;
        alarmSound.volume = 1.0;
        alarmSound.playbackRate = 1.18;
        alarmSound.play().catch(e => console.log("Looping alarm unavailable", e));
    };

    // Enable Monitoring
    startBtn.addEventListener('click', () => {
        startOverlay.classList.add('hidden');
        monitoringActive = true;
        audioCtx.resume();
        speak("System active.");

        alarmSound.play().then(() => {
            alarmSound.pause();
            alarmSound.currentTime = 0;
        }).catch(e => console.log("Unlock failed", e));
    });

    const speak = (text, interrupt = false) => {
        if (!monitoringActive) return;
        if (interrupt) {
            window.speechSynthesis.cancel();
        }
        const msg = new SpeechSynthesisUtterance(text);
        msg.rate = 1.3; // Slightly faster for urgency
        window.speechSynthesis.speak(msg);
    };

    setInterval(() => {
        clockElement.innerText = new Date().toLocaleTimeString();
    }, 1000);

    // Polling Backend
    setInterval(async () => {
        if (!monitoringActive) return;

        try {
            const response = await fetch('/status');
            const data = await response.json();
            const status = data.status;

            // 1. SPECIFIC ALERTS (Highest Priority)
            let specificHandled = false;

            if (status.includes("DANGER: PHONE USAGE")) {
                statusIcon.innerText = "🚨";
                statusTitle.innerText = "PHONE DANGER";
                statusDesc.innerText = "PULL OVER IMMEDIATELY!";
                body.className = "status-danger";
                drowsyOverlay.classList.add('visible');
                playDangerAlarm();
                specificHandled = true;
                isDanger = true;
            }
            else if (status.includes("NO FACE SEEN")) {
                statusIcon.innerText = "👤";
                statusTitle.innerText = "NO DRIVER";
                statusDesc.innerText = "Camera cannot see driver!";
                body.className = "status-danger";
                drowsyOverlay.classList.remove('visible');
                playDangerAlarm();
                isDanger = true;
                specificHandled = true;
            }
            else if (status.includes("PHONE USAGE (WARNING)")) {
                statusIcon.innerText = "📵";
                statusTitle.innerText = "PHONE DETECTED";
                statusDesc.innerText = "Please put your phone away";
                body.className = "status-warning";
                if (Date.now() - lastVoiceAlert > VOICE_COOLDOWN) {
                    lastVoiceAlert = Date.now();
                    speak("Warning! Please put your phone away.");
                }
                specificHandled = true;
            }
            else if (status.includes("WARNING: SLEEPY")) {
                statusIcon.innerText = "🥱";
                statusTitle.innerText = "SLEEPY";
                statusDesc.innerText = "Wake up!";
                body.className = "status-warning";
                if (Date.now() - lastVoiceAlert > VOICE_COOLDOWN) {
                    lastVoiceAlert = Date.now();
                    speak("Warning! Please stay awake.");
                }
                specificHandled = true;
            }
            else if (status.includes("YAWNING")) {
                statusIcon.innerText = "🥱";
                statusTitle.innerText = "SLEEPY";
                statusDesc.innerText = "Frequent yawning detected";
                body.className = "status-warning";
                if (Date.now() - lastVoiceAlert > VOICE_COOLDOWN) {
                    lastVoiceAlert = Date.now();
                    speak("Warning! Stay awake.");
                }
                specificHandled = true;
            }
            else if (status.includes("DANGER: HEAD DOWN")) {
                statusIcon.innerText = "🚨";
                statusTitle.innerText = "HEAD DANGER";
                statusDesc.innerText = "LOOK AT THE ROAD!";
                body.className = "status-danger";
                drowsyOverlay.classList.add('visible');
                playDangerAlarm();
                specificHandled = true;
                isDanger = true;
            }
            else if (status.includes("DANGER: ROAD ATTENTION LOST")) {
                statusIcon.innerText = "🚨";
                statusTitle.innerText = "ATTENTION LOST";
                statusDesc.innerText = "LOOK AHEAD IMMEDIATELY!";
                body.className = "status-danger";
                drowsyOverlay.classList.add('visible');
                playDangerAlarm();
                specificHandled = true;
                isDanger = true;
            }
            else if (status.includes("EYE GAZE DIVERTED DOWN")) {
                statusIcon.innerText = "👀";
                statusTitle.innerText = "GAZE DOWN";
                statusDesc.innerText = "Keep your eyes on the road!";
                body.className = "status-warning";
                if (Date.now() - lastVoiceAlert > VOICE_COOLDOWN) {
                    lastVoiceAlert = Date.now();
                    speak("Eyes on the road!", true);
                }
                specificHandled = true;
            }
            else if (status.includes("REPEATED SIDE GLANCES")) {
                statusIcon.innerText = "👀";
                statusTitle.innerText = "SIDE GLANCES";
                statusDesc.innerText = "Focus straight ahead!";
                body.className = "status-warning";
                if (Date.now() - lastVoiceAlert > VOICE_COOLDOWN) {
                    lastVoiceAlert = Date.now();
                    speak("Warning! Focus straight ahead.", true);
                }
                specificHandled = true;
            }
            else if (status.includes("ROAD ATTENTION WARNING")) {
                statusIcon.innerText = "👀";
                statusTitle.innerText = "ATTENTION";
                statusDesc.innerText = "Return attention to road!";
                body.className = "status-warning";
                if (Date.now() - lastVoiceAlert > VOICE_COOLDOWN) {
                    lastVoiceAlert = Date.now();
                    speak("Warning! Look ahead.", true);
                }
                specificHandled = true;
            }
            else if (status.includes("HEAD DISTRACTION")) {
                statusIcon.innerText = "👀";
                statusTitle.innerText = "LOOK UP";
                statusDesc.innerText = "Eyes off the road!";
                body.className = "status-warning";
                if (Date.now() - lastVoiceAlert > VOICE_COOLDOWN) {
                    lastVoiceAlert = Date.now();
                    speak("Eyes on the road!", true);
                }
                specificHandled = true;
            }

            // 2. GENERIC ALERTS (Only if no specific alert is handled)
            if (!specificHandled) {
                if (status.includes("SAFE")) {
                    if (isDanger) {
                        isDanger = false;
                        alarmSound.pause();
                        alarmSound.currentTime = 0;
                    }
                    body.className = "status-safe";
                    statusIcon.innerText = "✅";
                    statusTitle.innerText = "SAFE";
                    statusDesc.innerText = "Monitoring...";
                    drowsyOverlay.classList.remove('visible');
                }
                else if (status.includes("DANGER")) {
                    body.className = "status-danger";
                    statusIcon.innerText = "🛑";
                    statusTitle.innerText = "DANGER!";
                    statusDesc.innerText = status;
                    drowsyOverlay.classList.add('visible');
                    playDangerAlarm();
                    if (!isDanger) {
                        isDanger = true;
                    }
                }
                else if (status.includes("WARNING")) {
                    isDanger = false;
                    body.className = "status-warning";
                    statusIcon.innerText = "⚠️";
                    statusTitle.innerText = "WARNING";
                    statusDesc.innerText = status;
                    drowsyOverlay.classList.remove('visible');
                    alarmSound.pause();
                    if (Date.now() - lastVoiceAlert > 5000) {
                        lastVoiceAlert = Date.now();
                        speak("Please stay alert.");
                    }
                }
            }

            // Fallback description showing all status labels
            if (!status.includes("SAFE") && statusDesc.innerText.includes("Monitoring")) {
                statusDesc.innerText = status;
            }

        } catch (e) {
            console.error("Fetch error", e);
        }
    }, 250); // High-speed polling for millisecond alerts
});
