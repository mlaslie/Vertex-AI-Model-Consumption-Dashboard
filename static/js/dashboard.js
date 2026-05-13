const PALETTE = [
    "#38bdf8", "#a78bfa", "#4ade80", "#fbbf24", "#f87171",
    "#fb923c", "#22d3ee", "#e879f9", "#34d399", "#facc15",
];

const CODE_COLORS = {
    "200": "#4ade80",
    "204": "#22d3ee",
    "400": "#fbbf24",
    "401": "#fb923c",
    "403": "#f97316",
    "404": "#a78bfa",
    "429": "#facc15",
    "499": "#f87171",
    "500": "#ef4444",
    "503": "#dc2626",
};

const colorFor = (key, idx) => CODE_COLORS[key] || PALETTE[idx % PALETTE.length];

const charts = {};
let refreshTimer = null;

const $ = (id) => document.getElementById(id);

// Pick a bucket size that gives a sensible number of points per window.
function alignmentFor(windowSec) {
    const w = parseInt(windowSec, 10);
    if (w <= 6 * 3600) return 60;        // ≤6h  → 1-minute buckets
    if (w <= 24 * 3600) return 300;      // 24h  → 5-minute buckets
    if (w <= 7 * 86400) return 3600;     // 7d   → 1-hour buckets
    return 86400;                         // 30d  → 1-day buckets
}

function timeUnitFor(alignSec) {
    if (alignSec >= 86400) return "day";
    if (alignSec >= 3600) return "hour";
    return "minute";
}

const params = () => {
    const window_seconds = $("window").value;
    const model = $("model").value;
    const alignment_seconds = alignmentFor(window_seconds);
    return {
        window_seconds,
        alignment_seconds,
        ...(model ? { model } : {}),
    };
};

const qs = (obj) => new URLSearchParams(obj).toString();

async function getJSON(path, query) {
    const url = `${path}?${qs(query)}`;
    const r = await fetch(url);
    if (!r.ok) {
        const body = await r.text();
        throw new Error(`${r.status} ${r.statusText}: ${body}`);
    }
    return r.json();
}

function setStatus(msg, level = "info") {
    const el = $("status");
    el.textContent = msg;
    el.style.color = level === "error" ? "var(--bad)" : "var(--muted)";
}

function renderSummary(s) {
    $("card-total").textContent = s.total_requests.toLocaleString();
    $("card-qpm").textContent = s.avg_qpm.toLocaleString();
    $("card-success").textContent = `${s.success_rate_pct}%`;
    $("card-input").textContent = s.avg_input_tokens.toLocaleString();
    $("card-output").textContent = s.avg_output_tokens.toLocaleString();
}

function destroyChart(key) {
    if (charts[key]) {
        charts[key].destroy();
        delete charts[key];
    }
}

const baseLine = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
        legend: { labels: { color: "#e2e8f0" } },
        tooltip: { mode: "index", intersect: false },
    },
    scales: {
        x: {
            type: "time",
            time: { unit: "minute" },
            ticks: { color: "#94a3b8", maxRotation: 0, autoSkip: true },
            grid: { color: "#27344944" },
        },
        y: {
            beginAtZero: true,
            ticks: { color: "#94a3b8" },
            grid: { color: "#27344944" },
        },
    },
};

const basePie = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { labels: { color: "#e2e8f0" } } },
};

function pointsToXY(points, { dropZeros = false } = {}) {
    const out = [];
    for (const p of points) {
        if (dropZeros && !p.value) continue;
        out.push({ x: p.timestamp * 1000, y: p.value });
    }
    return out;
}

function lineOptions(alignSec, yLabel) {
    const unit = timeUnitFor(alignSec);
    const tooltipFormat = unit === "day" ? "DDD" : (unit === "hour" ? "DDD HH:mm" : "DDD HH:mm");
    return {
        ...baseLine,
        scales: {
            ...baseLine.scales,
            x: {
                ...baseLine.scales.x,
                time: { unit, tooltipFormat },
            },
            y: {
                ...baseLine.scales.y,
                title: { display: true, text: yLabel, color: "#94a3b8" },
            },
        },
    };
}

function renderQPM(series, alignSec) {
    destroyChart("qpm");
    // For daily buckets, only plot days that actually had traffic.
    const dropZeros = alignSec >= 86400;
    const datasets = series.map((s, i) => ({
        label: s.label,
        data: pointsToXY(s.points, { dropZeros }),
        borderColor: PALETTE[i % PALETTE.length],
        backgroundColor: PALETTE[i % PALETTE.length] + "33",
        tension: 0.25,
        pointRadius: dropZeros ? 4 : 2,
        borderWidth: 2,
        spanGaps: true,
    }));
    charts.qpm = new Chart($("qpm-chart"), {
        type: "line",
        data: { datasets },
        options: lineOptions(alignSec, "Queries / minute"),
    });
}

function renderTokens(grouped, alignSec) {
    destroyChart("tokens");
    const datasets = [];
    let idx = 0;
    for (const [ttype, seriesList] of Object.entries(grouped)) {
        for (const s of seriesList) {
            datasets.push({
                label: `${s.label} (${ttype})`,
                data: pointsToXY(s.points),
                borderColor: PALETTE[idx % PALETTE.length],
                backgroundColor: PALETTE[idx % PALETTE.length] + "33",
                borderDash: ttype === "input" ? [] : [6, 4],
                tension: 0.25,
                pointRadius: 2,
                borderWidth: 2,
            });
            idx++;
        }
    }
    charts.tokens = new Chart($("tokens-chart"), {
        type: "line",
        data: { datasets },
        options: lineOptions(alignSec, "Tokens / minute"),
    });
}

function renderCodes(codes) {
    destroyChart("codes");
    const labels = Object.keys(codes).sort();
    const data = labels.map((k) => codes[k]);
    const colors = labels.map((k, i) => colorFor(k, i));
    charts.codes = new Chart($("codes-chart"), {
        type: "doughnut",
        data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0 }] },
        options: basePie,
    });
}

function renderDoughnut(canvasId, chartKey, byKey) {
    destroyChart(chartKey);
    const entries = Object.entries(byKey).sort((a, b) => b[1] - a[1]);
    const labels = entries.map((e) => e[0]);
    const data = entries.map((e) => e[1]);
    const colors = labels.map((_, i) => PALETTE[i % PALETTE.length]);
    charts[chartKey] = new Chart($(canvasId), {
        type: "doughnut",
        data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 0 }] },
        options: basePie,
    });
    return labels;
}

function renderModels(byModel) {
    const labels = renderDoughnut("models-chart", "models", byModel);
    refreshModelOptions(labels);
}

function renderRegions(byRegion) {
    renderDoughnut("regions-chart", "regions", byRegion);
}

function fmtNum(n, decimals = 2) {
    if (n == null || Number.isNaN(n)) return "—";
    if (Math.abs(n) >= 1000) {
        return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
    }
    return n.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
    });
}

function renderModelStats(rows) {
    const tbody = document.querySelector("#model-stats-table tbody");
    tbody.innerHTML = "";
    if (!rows.length) {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.colSpan = 8;
        td.textContent = "No data in this window.";
        td.style.color = "var(--muted)";
        td.style.textAlign = "center";
        td.style.padding = "20px";
        tr.appendChild(td);
        tbody.appendChild(tr);
        return;
    }
    for (const r of rows) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${r.model}</td>
            <td class="num">${r.total_queries.toLocaleString()}</td>
            <td class="num">${fmtNum(r.avg_qps, 4)}</td>
            <td class="num">${fmtNum(r.peak_qps, 4)}</td>
            <td class="num">${fmtNum(r.avg_qpm)}</td>
            <td class="num">${fmtNum(r.peak_qpm)}</td>
            <td class="num">${fmtNum(r.avg_input_tpm)}</td>
            <td class="num">${fmtNum(r.peak_input_tpm)}</td>
        `;
        tbody.appendChild(tr);
    }
}

function refreshModelOptions(models) {
    const select = $("model");
    const current = select.value;
    const seen = new Set(models);
    [...select.options].forEach((opt, i) => {
        if (i === 0) return;
        if (!seen.has(opt.value)) opt.remove();
    });
    const existing = new Set([...select.options].map((o) => o.value));
    for (const m of models) {
        if (!existing.has(m)) {
            const opt = document.createElement("option");
            opt.value = m;
            opt.textContent = m;
            select.appendChild(opt);
        }
    }
    if (current && [...select.options].some((o) => o.value === current)) {
        select.value = current;
    }
}

async function reload() {
    const p = params();
    setStatus("Loading…");
    try {
        const [summary, qpm, tokens, codes, byModel, byRegion, modelStats] = await Promise.all([
            getJSON("/api/summary", { window_seconds: p.window_seconds }),
            getJSON("/api/qpm", p),
            getJSON("/api/tokens", p),
            getJSON("/api/response-codes", p),
            getJSON("/api/requests-by-model", { window_seconds: p.window_seconds }),
            getJSON("/api/requests-by-region", { window_seconds: p.window_seconds }),
            getJSON("/api/model-stats", { window_seconds: p.window_seconds }),
        ]);
        renderSummary(summary);
        renderQPM(qpm, p.alignment_seconds);
        renderTokens(tokens, p.alignment_seconds);
        renderCodes(codes);
        renderModels(byModel);
        renderRegions(byRegion);
        renderModelStats(modelStats);
        const ts = new Date().toLocaleTimeString();
        if (summary.total_requests === 0) {
            setStatus(
                `No Vertex AI traffic in this window. Make a Gemini call, then wait ~5 min for metrics. (Updated ${ts})`,
            );
        } else {
            setStatus(`Updated ${ts}`);
        }
    } catch (e) {
        console.error(e);
        setStatus(`Error: ${e.message}`, "error");
    }
}

function scheduleRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    const ms = parseInt($("refresh").value, 10);
    if (ms > 0) refreshTimer = setInterval(reload, ms);
}

function loadScript(src) {
    return new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = src;
        s.onload = resolve;
        s.onerror = () => reject(new Error(`Failed to load ${src}`));
        document.head.appendChild(s);
    });
}

async function ensureTimeAdapter() {
    if (window.luxon) return;
    await loadScript("https://cdn.jsdelivr.net/npm/luxon@3.5.0/build/global/luxon.min.js");
    await loadScript("https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1.3.1/dist/chartjs-adapter-luxon.umd.min.js");
}

(async function init() {
    await ensureTimeAdapter();
    $("reload").addEventListener("click", reload);
    $("window").addEventListener("change", reload);
    $("model").addEventListener("change", reload);
    $("refresh").addEventListener("change", scheduleRefresh);
    await reload();
    scheduleRefresh();
})();
