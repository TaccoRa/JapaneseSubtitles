console.log("Video Sync Extension geladen:", window.location.href);

const SERVER_URL = "http://127.0.0.1:5001/time";

let lastSent = 0;
let currentVideo = null;

function safeFetch(data) {
    fetch(SERVER_URL, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify(data)
    }).catch(() => {
        // silent fail to avoid spam
    });
}

function sendTime(video) {
    if (!video) return;

    const now = Date.now();
    if (now - lastSent < 250) return; // throttle (4x/sec max)
    lastSent = now;

    const currentTime = video.currentTime;
    const duration = video.duration;

    if (!isFinite(currentTime) || !isFinite(duration)) return;

    safeFetch({
        currentTime,
        duration
    });
}

function findBestVideo() {
    const videos = Array.from(document.querySelectorAll("video"));

    if (videos.length === 0) return null;

    // pick most "active" video
    let best = videos[0];

    for (const v of videos) {
        if ((v.readyState || 0) > (best.readyState || 0)) {
            best = v;
        }
    }

    return best;
}

function attach(video) {
    if (!video || video === currentVideo) return;

    currentVideo = video;

    console.log("Video verbunden:", video);

    video.addEventListener("timeupdate", () => sendTime(video));
    video.addEventListener("seeked", () => sendTime(video));
    video.addEventListener("play", () => sendTime(video));
    video.addEventListener("pause", () => sendTime(video));
    video.addEventListener("loadedmetadata", () => sendTime(video));

    sendTime(video);
}

function scanForVideo() {
    const video = findBestVideo();
    if (video) attach(video);
}

// initial scan
scanForVideo();

// keep scanning (important for dynamic sites)
setInterval(() => {
    scanForVideo();

    if (currentVideo) {
        sendTime(currentVideo);
    }
}, 1000);

// detect DOM changes (video replacement sites)
const observer = new MutationObserver(() => {
    scanForVideo();
});

observer.observe(document.documentElement, {
    childList: true,
    subtree: true
});