/* ═══════════════════════════════════════════════════
   Premier League Dashboard – Frontend Logic
   ═══════════════════════════════════════════════════ */

const API = "";
let STATE = {
    teams: [],
    gameweeks: [],
    currentGW: 1,
    selectedGW: 1,
    compareN: 5,
    charts: {},
    guessGW: null,
    guessData: {},
    guessAdvice: {},
    league: sessionStorage.getItem("plLeague") || "pl",
    gwLabel: "GW",
};

const LEAGUE_THEMES = {
    pl: { name: "Premier League", brand: "PL Dashboard", gwLabel: "GW",
          cssClass: "", logo: "/static/pl-logo.png" },
    laliga: { name: "La Liga", brand: "La Liga Dashboard", gwLabel: "MD",
              cssClass: "laliga-theme", logo: "/static/pl-logo.png" },
};

// ── Helpers ──

async function api(path) {
    const sep = path.includes("?") ? "&" : "?";
    const url = `${API}${path}${sep}league=${STATE.league}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
}

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return document.querySelectorAll(sel); }

function hideLoading() {
    const overlay = $("#loadingOverlay");
    overlay.classList.add("hidden");
    setTimeout(() => overlay.style.display = "none", 500);
}

function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

// ── Tab Navigation ──

function initTabs() {
    $$("[data-tab]").forEach(link => {
        link.addEventListener("click", e => {
            e.preventDefault();
            const tab = link.dataset.tab;
            $$("[data-tab]").forEach(l => l.classList.remove("active"));
            link.classList.add("active");
            $$(".tab-content").forEach(s => s.classList.remove("active"));
            $(`#tab-${tab}`).classList.add("active");

            // Collapse mobile nav
            const navCollapse = bootstrap.Collapse.getInstance($("#navContent"));
            if (navCollapse) navCollapse.hide();

            sessionStorage.setItem("plTab", tab);
            if (tab === "results") loadStandings();
            if (tab === "guess") loadGuessTab();
        });
    });
}

// ── Gameweek Pills ──

function renderGWPills() {
    const container = $("#gwPills");
    container.innerHTML = "";
    const label = STATE.gwLabel || "GW";
    STATE.gameweeks.forEach(gw => {
        const pill = document.createElement("button");
        pill.className = "gw-pill" +
            (gw.id === STATE.selectedGW ? " active" : "") +
            (gw.is_current ? " current" : "") +
            (gw.is_next ? " next-live" : "");
        pill.textContent = `${label}${gw.id}`;
        pill.addEventListener("click", () => selectGW(gw.id));
        container.appendChild(pill);
    });
    scrollToActiveGW();
}

function scrollToActiveGW() {
    const wrapper = $("#gwPills");
    const active = wrapper.querySelector(".gw-pill.active");
    if (active) {
        active.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
    }
}

function getActiveTab() {
    const el = document.querySelector(".tab-content.active");
    return el ? el.id.replace("tab-", "") : "results";
}

function selectGW(gw) {
    STATE.selectedGW = gw;
    STATE.guessGW = gw;
    guessPredictionsLoading = false;
    sessionStorage.setItem("plGW", gw);
    renderGWPills();
    loadFixtures(gw);
    const tab = getActiveTab();
    if (tab === "results") { standingsLoaded = false; loadStandings(); }
    if (tab === "guess") {
        STATE.guessAdvice = {};
        loadGuessCards();
        loadGuessHistory();
        const activeSub = document.querySelector(".guess-subtab.active");
        const sub = activeSub ? activeSub.dataset.subtab : "fill";
        if (sub === "predictions") {
            loadGuessPredictions();
            loadBestBetsScore();
        } else if (sub === "compare") {
            loadGuessComparison();
        }
    }
}

function initGWNav() {
    $("#gwPrev").addEventListener("click", () => {
        if (STATE.selectedGW > 1) selectGW(STATE.selectedGW - 1);
    });
    $("#gwNext").addEventListener("click", () => {
        const max = STATE.gameweeks.length;
        if (STATE.selectedGW < max) selectGW(STATE.selectedGW + 1);
    });
}

// ── Fixtures / Results ──

let _liveRefreshTimer = null;

async function loadFixtures(gw, isLiveRefresh) {
    const container = $("#fixturesContainer");
    if (!isLiveRefresh) {
        container.innerHTML = `<div class="col-12 text-center py-4">
            <div class="spinner-border text-light spinner-border-sm"></div>
        </div>`;
    }

    try {
        const data = await api(`/api/fixtures/${gw}?live=1`);
        if (!data.fixtures || data.fixtures.length === 0) {
            container.innerHTML = `<div class="col-12 text-center py-4 text-muted">No fixtures for this gameweek</div>`;
            return;
        }
        container.innerHTML = data.fixtures.map(renderMatchCard).join("");

        clearInterval(_liveRefreshTimer);
        if (data.has_live) {
            _liveRefreshTimer = setInterval(() => loadFixtures(STATE.selectedGW, true), 3000);
        }
    } catch (err) {
        if (!isLiveRefresh) {
            container.innerHTML = `<div class="col-12 text-center py-4 text-danger">Error loading fixtures</div>`;
        }
    }
}

function renderMatchCard(m) {
    const finished = m.finished;
    const isLive = m.is_live;
    const homeWin = finished && m.home_score > m.away_score;
    const awayWin = finished && m.away_score > m.home_score;

    let scoreContent;
    if (finished || m.started) {
        const hs = m.home_score != null ? m.home_score : "–";
        const as = m.away_score != null ? m.away_score : "–";
        scoreContent = `
            <span class="score-num ${homeWin ? "winner" : ""}">${hs}</span>
            <span class="dash">–</span>
            <span class="score-num ${awayWin ? "winner" : ""}">${as}</span>`;
    } else {
        scoreContent = `<span>${formatDate(m.kickoff_time) || "TBD"}</span>`;
    }

    let liveTag = "";
    if (isLive) {
        const min = m.minutes || 0;
        const minText = min === 45 ? "HT" : min >= 90 ? "90+" : `${min}'`;
        liveTag = `<div class="live-match-tag"><span class="live-dot"></span>${minText}</div>`;
    } else if (finished) {
        liveTag = `<div class="ft-tag">FT</div>`;
    }

    return `
    <div class="col-12">
        <div class="match-card ${!finished && !m.started ? "not-started" : ""} ${isLive ? "match-live" : ""}">
            ${liveTag}
            <div class="team-row">
                <div class="team-info">
                    <img class="team-badge" src="${m.home_badge}" alt="${m.home_short}" loading="lazy"
                         onerror="this.style.display='none'">
                    <span class="team-name">${m.home_team}</span>
                </div>
                <div class="score-box">${scoreContent}</div>
                <div class="team-info away">
                    <img class="team-badge" src="${m.away_badge}" alt="${m.away_short}" loading="lazy"
                         onerror="this.style.display='none'">
                    <span class="team-name">${m.away_team}</span>
                </div>
            </div>
        </div>
    </div>`;
}

// ── Standings ──

let standingsLoaded = false;
async function loadStandings() {
    const body = $("#standingsBody");
    if (!standingsLoaded) {
        body.innerHTML = `<tr><td colspan="11" class="text-center py-3">
            <div class="spinner-border text-light spinner-border-sm"></div></td></tr>`;
    }

    try {
        const [standingsData, teamsData] = await Promise.all([
            api(`/api/standings?live=1&gw=${STATE.selectedGW}`),
            Promise.resolve(STATE.teams.length ? { teams: STATE.teams } : api("/api/teams")),
        ]);

        const teamMap = {};
        teamsData.teams.forEach(t => teamMap[t.id] = t);

        const rows = standingsData.standings;
        const formPromises = rows.map(r =>
            api(`/api/team/${r.team.id}/form?n=5&before_gw=${STATE.selectedGW + 1}`).catch(() => null)
        );
        const forms = await Promise.all(formPromises);

        body.innerHTML = rows.map((r, i) => {
            const posClass = r.position === 1 ? "pos-champions" :
                r.position <= 4 ? "pos-cl" :
                r.position <= 6 ? "pos-el" :
                r.position >= 18 ? "pos-relegation" : "";

            let formHtml = "";
            if (forms[i] && forms[i].stats && forms[i].stats.matches) {
                const fm = forms[i].stats.matches.slice().reverse();
                formHtml = `<div class="form-dots">${fm.map(m =>
                    `<span class="form-dot ${m.result}">${m.result}</span>`
                ).join("")}</div>`;
            }

            return `<tr>
                <td class="pos-cell ${posClass}">${r.position}</td>
                <td>
                    <div class="team-cell">
                        <img src="${r.team.badge}" alt="" loading="lazy" onerror="this.style.display='none'">
                        <span>${r.team.name}</span>
                    </div>
                </td>
                <td>${r.played}</td>
                <td>${r.won}</td>
                <td>${r.drawn}</td>
                <td>${r.lost}</td>
                <td class="d-none d-sm-table-cell">${r.gf}</td>
                <td class="d-none d-sm-table-cell">${r.ga}</td>
                <td>${r.gd > 0 ? "+" + r.gd : r.gd}</td>
                <td class="pts-cell">${r.points}</td>
                <td class="d-none d-md-table-cell">${formHtml}</td>
            </tr>`;
        }).join("");

        standingsLoaded = true;
    } catch (err) {
        body.innerHTML = `<tr><td colspan="11" class="text-center text-danger py-3">Error loading standings</td></tr>`;
    }
}

// ── Compare ──

let compareTeam1 = null;
let compareTeam2 = null;

function initCompare() {
    renderBadgeGrid();

    $$("#comparePeriod .btn").forEach(btn => {
        btn.addEventListener("click", () => {
            $$("#comparePeriod .btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            STATE.compareN = parseInt(btn.dataset.n);
            if (compareTeam1 && compareTeam2) {
                loadComparison(compareTeam1.id, compareTeam2.id);
            }
        });
    });
}

function renderBadgeGrid() {
    const grid = $("#teamBadgesGrid");
    grid.innerHTML = STATE.teams.map(t => `
        <div class="badge-pick" data-team-id="${t.id}" data-team-name="${t.short_name}" data-team-badge="${t.badge}">
            <img src="${t.badge}" alt="${t.short_name}" loading="lazy" onerror="this.style.display='none'">
            <span class="badge-label">${t.short_name}</span>
        </div>
    `).join("");

    grid.querySelectorAll(".badge-pick").forEach(bp => {
        bp.addEventListener("click", () => handleBadgeClick(bp));
    });
}

function handleBadgeClick(bp) {
    const id = parseInt(bp.dataset.teamId);
    const name = bp.dataset.teamName;
    const badge = bp.dataset.teamBadge;
    const team = { id, name, badge };

    if (compareTeam1 && compareTeam1.id === id) {
        compareTeam1 = null;
        bp.classList.remove("selected-1");
        updateCompareSelection();
        return;
    }
    if (compareTeam2 && compareTeam2.id === id) {
        compareTeam2 = null;
        bp.classList.remove("selected-2");
        updateCompareSelection();
        return;
    }

    if (!compareTeam1) {
        compareTeam1 = team;
        $$(".badge-pick").forEach(b => b.classList.remove("selected-1"));
        bp.classList.add("selected-1");
    } else if (!compareTeam2) {
        compareTeam2 = team;
        $$(".badge-pick").forEach(b => b.classList.remove("selected-2"));
        bp.classList.add("selected-2");
    } else {
        $$(".badge-pick").forEach(b => b.classList.remove("selected-2"));
        compareTeam2 = team;
        bp.classList.add("selected-2");
    }

    updateCompareSelection();

    if (compareTeam1 && compareTeam2) {
        loadComparison(compareTeam1.id, compareTeam2.id);
    }
}

function updateCompareSelection() {
    const sel = $("#compareSelection");
    if (compareTeam1 || compareTeam2) {
        sel.classList.remove("d-none");
        const s1 = $("#compareSel1");
        const s2 = $("#compareSel2");
        if (compareTeam1) {
            s1.querySelector("img").src = compareTeam1.badge;
            s1.querySelector("span").textContent = compareTeam1.name;
            s1.style.opacity = "1";
        } else {
            s1.querySelector("span").textContent = "Pick a team";
            s1.querySelector("img").src = "";
            s1.style.opacity = ".4";
        }
        if (compareTeam2) {
            s2.querySelector("img").src = compareTeam2.badge;
            s2.querySelector("span").textContent = compareTeam2.name;
            s2.style.opacity = "1";
        } else {
            s2.querySelector("span").textContent = "Pick a team";
            s2.querySelector("img").src = "";
            s2.style.opacity = ".4";
        }
    } else {
        sel.classList.add("d-none");
    }

    if (!compareTeam1 || !compareTeam2) {
        $("#compareResult").classList.add("d-none");
    }
}

async function loadComparison(t1, t2) {
    const result = $("#compareResult");

    // Always rebuild the inner structure to avoid destroyed DOM issues
    result.innerHTML = `
        <div class="compare-header row g-0 mb-4">
            <div class="col-5 text-center">
                <img id="cmpBadge1" class="compare-badge" src="" alt="">
                <h5 id="cmpName1" class="mt-2 mb-0 fw-bold"></h5>
            </div>
            <div class="col-2 d-flex align-items-center justify-content-center">
                <span class="vs-badge-lg">VS</span>
            </div>
            <div class="col-5 text-center">
                <img id="cmpBadge2" class="compare-badge" src="" alt="">
                <h5 id="cmpName2" class="mt-2 mb-0 fw-bold"></h5>
            </div>
        </div>
        <div id="compareStats" class="mb-4"></div>
        <div class="row g-3 mb-4">
            <div class="col-md-6"><div class="card-dark p-3">
                <h6 class="text-center mb-3">Points (Last N Games)</h6>
                <canvas id="comparePointsChart"></canvas>
            </div></div>
            <div class="col-md-6"><div class="card-dark p-3">
                <h6 class="text-center mb-3">Goals For / Against</h6>
                <canvas id="compareGoalsChart"></canvas>
            </div></div>
        </div>
        <div class="row g-3">
            <div class="col-md-6"><div class="card-dark p-3">
                <h6 class="text-center mb-3" id="cmpFormTitle1">Recent Form</h6>
                <div id="cmpForm1"></div>
            </div></div>
            <div class="col-md-6"><div class="card-dark p-3">
                <h6 class="text-center mb-3" id="cmpFormTitle2">Recent Form</h6>
                <div id="cmpForm2"></div>
            </div></div>
        </div>`;
    result.classList.remove("d-none");

    // Reset chart references since canvases are new
    STATE.charts.points = null;
    STATE.charts.goals = null;

    try {
        const data = await api(`/api/compare/${t1}/${t2}?n=${STATE.compareN}`);
        const info1 = data.team1.info;
        const info2 = data.team2.info;
        const stats1 = data.team1.stats;
        const stats2 = data.team2.stats;

        $("#cmpBadge1").src = info1.badge;
        $("#cmpBadge2").src = info2.badge;
        $("#cmpName1").textContent = info1.name;
        $("#cmpName2").textContent = info2.name;
        $("#cmpFormTitle1").textContent = `${info1.short_name} Recent Form`;
        $("#cmpFormTitle2").textContent = `${info2.short_name} Recent Form`;

        renderCompareStats(stats1, stats2);
        renderCompareCharts(info1, info2, stats1, stats2);
        renderCompareForm("cmpForm1", stats1.matches);
        renderCompareForm("cmpForm2", stats2.matches);
    } catch (err) {
        console.error("Compare error:", err);
        result.innerHTML = `<div class="text-center text-danger py-3">Error: ${err.message}</div>`;
    }
}

function renderCompareStats(s1, s2) {
    const container = $("#compareStats");
    const metrics = [
        { label: "Points", v1: s1.points, v2: s2.points },
        { label: "Wins", v1: s1.wins, v2: s2.wins },
        { label: "Goals For", v1: s1.goals_for, v2: s2.goals_for },
        { label: "Goals Against", v1: s1.goals_against, v2: s2.goals_against, invert: true },
        { label: "Goal Diff", v1: s1.goal_diff || 0, v2: s2.goal_diff || 0 },
        { label: "Form %", v1: s1.form_score, v2: s2.form_score },
    ];

    container.innerHTML = metrics.map(m => {
        const max = Math.max(Math.abs(m.v1), Math.abs(m.v2), 1);
        const p1 = Math.abs(m.v1) / max * 100;
        const p2 = Math.abs(m.v2) / max * 100;
        const better1 = m.invert ? m.v1 < m.v2 : m.v1 > m.v2;
        const better2 = m.invert ? m.v2 < m.v1 : m.v2 > m.v1;

        return `<div class="stat-bar-row">
            <span class="stat-val ${better1 ? "better" : ""}">${m.v1}</span>
            <div class="stat-bar-container">
                <div class="stat-bar team1" style="width:${p1}%"></div>
            </div>
            <span class="stat-label">${m.label}</span>
            <div class="stat-bar-container">
                <div class="stat-bar team2" style="width:${p2}%"></div>
            </div>
            <span class="stat-val ${better2 ? "better" : ""}">${m.v2}</span>
        </div>`;
    }).join("");
}

function renderCompareCharts(info1, info2, stats1, stats2) {
    if (STATE.charts.points) STATE.charts.points.destroy();
    if (STATE.charts.goals) STATE.charts.goals.destroy();

    const ctx1 = $("#comparePointsChart").getContext("2d");
    STATE.charts.points = new Chart(ctx1, {
        type: "bar",
        data: {
            labels: [info1.short_name, info2.short_name],
            datasets: [{
                label: "Points",
                data: [stats1.points, stats2.points],
                backgroundColor: ["#00ff87", "#04f5ff"],
                borderRadius: 6,
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: true, grid: { color: "rgba(255,255,255,0.05)" }, ticks: { color: "#a89aad" } },
                x: { grid: { display: false }, ticks: { color: "#f0eaf2" } }
            }
        }
    });

    const ctx2 = $("#compareGoalsChart").getContext("2d");
    STATE.charts.goals = new Chart(ctx2, {
        type: "bar",
        data: {
            labels: [info1.short_name, info2.short_name],
            datasets: [
                {
                    label: "Goals For",
                    data: [stats1.goals_for, stats2.goals_for],
                    backgroundColor: ["#00ff87", "#04f5ff"],
                    borderRadius: 6,
                },
                {
                    label: "Goals Against",
                    data: [stats1.goals_against, stats2.goals_against],
                    backgroundColor: ["rgba(0,255,135,0.3)", "rgba(4,245,255,0.3)"],
                    borderRadius: 6,
                }
            ]
        },
        options: {
            responsive: true,
            plugins: { legend: { labels: { color: "#a89aad" } } },
            scales: {
                y: { beginAtZero: true, grid: { color: "rgba(255,255,255,0.05)" }, ticks: { color: "#a89aad" } },
                x: { grid: { display: false }, ticks: { color: "#f0eaf2" } }
            }
        }
    });
}

function renderCompareForm(containerId, matches) {
    const el = document.getElementById(containerId);
    if (!matches || matches.length === 0) {
        el.innerHTML = `<div class="text-center text-muted small py-2">No recent matches</div>`;
        return;
    }
    el.innerHTML = matches.map(m => {
        const color = m.result === "W" ? "W" : m.result === "D" ? "D" : "L";
        return `<div class="form-match-row">
            <span class="gw-label">GW${m.gameweek}</span>
            <span class="match-result-badge form-dot ${color}">${m.result}</span>
            <span class="match-score">${m.goals_for}–${m.goals_against}</span>
            <span class="match-opponent">${m.opponent_short}</span>
            <span class="match-venue">${m.is_home ? "H" : "A"}</span>
        </div>`;
    }).join("");
}

// ── Predictions (used under Guess tab) ──

function renderPredCard(p) {
    const m = p.match;
    const pred = p.prediction;
    const isLive = m.is_live;
    const isTopBet = p.is_top_bet;
    const betRank = p.bet_rank;

    let liveInfo = "";
    if (isLive) {
        const min = m.minutes || 0;
        const minText = min === 45 ? "HT" : min >= 90 ? "90+" : `${min}'`;
        const hs = m.home_score != null ? m.home_score : 0;
        const as = m.away_score != null ? m.away_score : 0;
        liveInfo = `<div class="pred-live-score"><span class="live-match-tag" style="position:static;margin-bottom:.4rem"><span class="live-dot"></span>${minText}</span> <b>${hs} - ${as}</b></div>`;
    } else if (m.finished) {
        const hs = m.home_score != null ? m.home_score : "-";
        const as = m.away_score != null ? m.away_score : "-";
        liveInfo = `<div class="pred-live-score"><span class="ft-tag" style="position:static;margin-bottom:.4rem">FT</span> <b>${hs} - ${as}</b></div>`;
    }

    const betBadge = isTopBet ? `<span class="top-bet-badge">${betRank} Best Bet</span>` : "";

    const adviceData = STATE.guessAdvice[m.id];
    let adviceHtml = "";
    if (adviceData) {
        const labels = { home: m.home_short, draw: "Draw", away: m.away_short };
        const isDrawScore = adviceData.recommended_home_score === adviceData.recommended_away_score;
        const adviceWinnerLabel = isDrawScore ? "Draw" : labels[adviceData.recommended_winner];
        const reasonsJson = adviceData.reasons ? encodeURIComponent(JSON.stringify(adviceData.reasons)) : "";
        const matchTitle = `${m.home_short} vs ${m.away_short}`;
        adviceHtml = `<div class="d-flex align-items-center gap-2 mt-2">
            <div class="guess-advice-tag" style="flex:1">
                <i class="bi bi-lightbulb-fill"></i>
                Advice: ${adviceWinnerLabel} (${adviceData.recommended_home_score}-${adviceData.recommended_away_score})
            </div>
            <button class="btn-why" onclick="showReasoning('${matchTitle}', '${reasonsJson}')" title="Why this prediction?">
                <i class="bi bi-info-circle-fill"></i>
            </button>
        </div>`;
    }

    return `
    <div class="col-12 col-sm-6 col-lg-4">
        <div class="pred-card ${isLive ? "match-live" : ""} ${isTopBet ? "top-bet" : ""}">
            ${betBadge}
            <div class="pred-teams">
                <div class="pred-team">
                    <img src="${m.home_badge}" alt="" loading="lazy" onerror="this.style.display='none'">
                    <span class="pred-team-name">${m.home_short}</span>
                </div>
                <span class="vs-badge">VS</span>
                <div class="pred-team">
                    <img src="${m.away_badge}" alt="" loading="lazy" onerror="this.style.display='none'">
                    <span class="pred-team-name">${m.away_short}</span>
                </div>
            </div>
            ${liveInfo}
            <div class="pred-bar-wrap">
                <div class="pred-bar-label small text-muted">AI</div>
                <div class="pred-bar-container">
                    <div class="pred-bar-home" style="width:${pred.home_win_pct}%">${pred.home_win_pct}%</div>
                    <div class="pred-bar-draw" style="width:${pred.draw_pct}%">${pred.draw_pct}%</div>
                    <div class="pred-bar-away" style="width:${pred.away_win_pct}%">${pred.away_win_pct}%</div>
                </div>
            </div>
            ${p.odds ? `
            <div class="pred-bar-wrap mt-1">
                <div class="pred-bar-label small text-muted">${p.odds.source || "Market"}</div>
                <div class="pred-bar-container pred-bar-odds">
                    <div class="pred-bar-home" style="width:${p.odds.home_pct}%">${p.odds.home_pct}%</div>
                    <div class="pred-bar-draw" style="width:${p.odds.draw_pct}%">${p.odds.draw_pct}%</div>
                    <div class="pred-bar-away" style="width:${p.odds.away_pct}%">${p.odds.away_pct}%</div>
                </div>
            </div>` : ""}
            <div class="pred-score-line">
                Predicted: <span>${pred.predicted_home_goals}</span> – <span>${pred.predicted_away_goals}</span>
            </div>
            ${adviceHtml}
        </div>
    </div>`;
}

// ── League Switching ──

function initLeagueToggle() {
    $$("#leagueToggle .league-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const league = btn.dataset.league;
            if (league === STATE.league) return;
            switchLeague(league);
        });
    });
}

function applyLeagueTheme(league) {
    const theme = LEAGUE_THEMES[league] || LEAGUE_THEMES.pl;
    STATE.gwLabel = theme.gwLabel;
    const brand = $("#brandText");
    if (brand) brand.textContent = theme.brand;
    document.body.classList.remove("laliga-theme");
    if (theme.cssClass) document.body.classList.add(theme.cssClass);
    $$("#leagueToggle .league-btn").forEach(b => {
        b.classList.toggle("active", b.dataset.league === league);
    });
}

async function switchLeague(league) {
    STATE.league = league;
    sessionStorage.setItem("plLeague", league);
    sessionStorage.removeItem("plGW");
    applyLeagueTheme(league);

    const overlay = $("#loadingOverlay");
    overlay.style.display = "flex";
    overlay.classList.remove("hidden");
    const loadText = $("#loadingText");
    if (loadText) loadText.textContent = `Loading ${(LEAGUE_THEMES[league] || {}).name || "league"} data...`;

    compareTeam1 = null;
    compareTeam2 = null;
    STATE.guessData = {};
    STATE.guessAdvice = {};
    STATE.charts = {};
    standingsLoaded = false;
    guessPredictionsLoading = false;

    try {
        const [teamsData, gwData] = await Promise.all([
            api("/api/teams"),
            api("/api/gameweeks"),
        ]);
        STATE.teams = teamsData.teams;
        STATE.gameweeks = gwData.gameweeks;
        STATE.currentGW = gwData.current_gameweek;
        STATE.gwLabel = gwData.gw_label || "GW";
        STATE.selectedGW = gwData.current_gameweek;
        STATE.guessGW = STATE.selectedGW;

        renderGWPills();
        initCompare();
        await Promise.all([loadFixtures(STATE.selectedGW), loadStandings()]);

        const activeTab = getActiveTab();
        if (activeTab === "guess") loadGuessTab();

        hideLoading();
    } catch (err) {
        console.error("League switch error:", err);
        hideLoading();
    }
}

// ── Init ──

async function init() {
    initTabs();
    initGWNav();
    initLeagueToggle();
    applyLeagueTheme(STATE.league);

    try {
        const [teamsData, gwData] = await Promise.all([
            api("/api/teams"),
            api("/api/gameweeks"),
        ]);

        STATE.teams = teamsData.teams;
        STATE.gameweeks = gwData.gameweeks;
        STATE.currentGW = gwData.current_gameweek;
        STATE.gwLabel = gwData.gw_label || "GW";

        const savedGW = parseInt(sessionStorage.getItem("plGW"));
        STATE.selectedGW = savedGW && savedGW >= 1 && savedGW <= STATE.gameweeks.length ? savedGW : gwData.current_gameweek;
        STATE.guessGW = STATE.selectedGW;

        renderGWPills();
        initCompare();
        initGuess();
        await Promise.all([loadFixtures(STATE.selectedGW), loadStandings()]);

        const savedTab = sessionStorage.getItem("plTab");
        if (savedTab && savedTab !== "results") {
            const tabLink = document.querySelector(`[data-tab="${savedTab}"]`);
            if (tabLink) tabLink.click();
        }
        const savedSub = sessionStorage.getItem("plSub");
        if (savedSub && savedTab === "guess") {
            const subBtn = document.querySelector(`.guess-subtab[data-subtab="${savedSub}"]`);
            if (subBtn) subBtn.click();
        }

        hideLoading();
        startLivePolling();
    } catch (err) {
        console.error("Init error:", err);
        hideLoading();
        document.body.innerHTML = `
            <div class="d-flex align-items-center justify-content-center min-vh-100">
                <div class="text-center">
                    <i class="bi bi-exclamation-triangle-fill text-warning" style="font-size:3rem"></i>
                    <h4 class="mt-3">Failed to load data</h4>
                    <p class="text-muted">Please check your internet connection and restart the app.</p>
                    <button class="btn btn-outline-light mt-2" onclick="location.reload()">
                        <i class="bi bi-arrow-clockwise me-1"></i>Retry
                    </button>
                </div>
            </div>`;
    }
}

// ── Guess Tab ──

function initGuess() {
    STATE.guessGW = STATE.selectedGW;
    $("#btnGetAdvice").addEventListener("click", loadAdvice);
    $("#btnSaveGuesses").addEventListener("click", saveGuesses);
    const autoBtn = $("#btnAutoFill");
    if (autoBtn) autoBtn.addEventListener("click", autoFillWithAI);
    const rndBtn = $("#btnRandomFill");
    if (rndBtn) rndBtn.addEventListener("click", randomFillGuesses);
    initGuessSubtabs();
}

function _rndGoal() { const r = Math.random(); if (r < .28) return 0; if (r < .62) return 1; if (r < .84) return 2; if (r < .94) return 3; return 4; }

async function randomFillGuesses() {
    const btn = $("#btnRandomFill");
    btn.disabled = true;
    const cards = document.querySelectorAll(".guess-card");
    let filled = 0;
    cards.forEach(card => {
        const mid = card.dataset.matchId;
        const hInput = card.querySelector('input[data-side="home"]');
        const aInput = card.querySelector('input[data-side="away"]');
        if (!hInput || !aInput || hInput.disabled) return;

        const h = _rndGoal(), a = _rndGoal();
        hInput.value = h;
        aInput.value = a;
        hInput.dispatchEvent(new Event("change", {bubbles: true}));
        aInput.dispatchEvent(new Event("change", {bubbles: true}));

        const w = h > a ? "home" : h === a ? "draw" : "away";
        const winBtn = card.querySelector(`.winner-btn[data-winner="${w}"]`);
        if (winBtn) winBtn.click();
        filled++;
    });
    btn.innerHTML = `<i class="bi bi-check-lg me-1"></i>${filled} filled!`;
    btn.classList.remove("btn-outline-danger");
    btn.classList.add("btn-outline-success");
    if (filled > 0) triggerAutoSave();
    setTimeout(() => {
        btn.innerHTML = '<i class="bi bi-shuffle me-1"></i>Random';
        btn.classList.remove("btn-outline-success");
        btn.classList.add("btn-outline-danger");
        btn.disabled = false;
    }, 1500);
}

async function autoFillWithAI() {
    const btn = $("#btnAutoFill");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Filling...';
    try {
        const gw = STATE.guessGW;
        const [fixData, predData, advData] = await Promise.all([
            api(`/api/fixtures/${gw}?live=1`),
            api(`/api/predictions/${gw}`).catch(() => ({ predictions: [] })),
            api(`/api/guess-advice/${gw}`).catch(() => ({ advice: [] })),
        ]);
        const fixtures = fixData.fixtures || [];
        const advMap = {};
        (advData.advice || []).forEach(a => advMap[a.match_id] = a);
        const predMap = {};
        (predData.predictions || []).forEach(p => predMap[p.match.id] = p);

        let filled = 0;
        fixtures.forEach(f => {
            if (f.finished || f.is_live) return;
            const card = document.querySelector(`.guess-card[data-match-id="${f.id}"]`);
            if (!card) return;
            const hInput = card.querySelector('input[data-side="home"]');
            const aInput = card.querySelector('input[data-side="away"]');
            if (hInput && hInput.value !== "" && aInput && aInput.value !== "") return;

            const adv = advMap[f.id];
            const pred = predMap[f.id]?.prediction;
            const hs = adv ? adv.recommended_home_score : pred ? pred.predicted_home : null;
            const as_ = adv ? adv.recommended_away_score : pred ? pred.predicted_away : null;
            const w = adv ? adv.recommended_winner : pred ? pred.recommended : null;

            if (hs == null || as_ == null || !w) return;

            if (hInput) { hInput.value = hs; hInput.dispatchEvent(new Event("change", {bubbles: true})); }
            if (aInput) { aInput.value = as_; aInput.dispatchEvent(new Event("change", {bubbles: true})); }

            const winBtn = card.querySelector(`.winner-btn[data-winner="${w}"]`);
            if (winBtn && !winBtn.classList.contains("selected-home") && !winBtn.classList.contains("selected-draw") && !winBtn.classList.contains("selected-away")) {
                winBtn.click();
            }
            filled++;
        });

        btn.innerHTML = filled > 0 ? `<i class="bi bi-check-lg me-1"></i>Filled ${filled} matches` : '<i class="bi bi-check-lg me-1"></i>All filled!';
        btn.classList.remove("btn-outline-info");
        btn.classList.add("btn-outline-success");
        if (filled > 0) triggerAutoSave();
        setTimeout(() => {
            btn.innerHTML = '<i class="bi bi-magic me-1"></i>Auto-fill AI';
            btn.classList.remove("btn-outline-success");
            btn.classList.add("btn-outline-info");
            btn.disabled = false;
        }, 2000);
    } catch (err) {
        btn.innerHTML = '<i class="bi bi-x-lg me-1"></i>Error';
        btn.disabled = false;
        setTimeout(() => { btn.innerHTML = '<i class="bi bi-magic me-1"></i>Auto-fill AI'; }, 2000);
    }
}

async function loadGuessTab() {
    await loadGuessCards();
    loadGuessHistory();
}

async function loadGuessCards() {
    const gw = STATE.guessGW;
    const container = $("#guessCardsContainer");
    container.innerHTML = `<div class="col-12 text-center py-4">
        <div class="spinner-border text-light spinner-border-sm"></div></div>`;

    try {
        const [fixData, guessData, scoreData, predData, adviceData] = await Promise.all([
            api(`/api/fixtures/${gw}?live=1`),
            api(`/api/guesses/${gw}`),
            api(`/api/guesses/${gw}/score`),
            api(`/api/predictions/${gw}`).catch(() => ({ predictions: [] })),
            api(`/api/guess-advice/${gw}`).catch(() => ({ advice: [] })),
        ]);

        const fixtures = fixData.fixtures || [];
        const saved = guessData.data?.guesses || [];
        const savedMap = {};
        saved.forEach(g => savedMap[g.match_id] = g);

        const scored = scoreData.has_guesses ? scoreData.results || [] : [];
        const scoredMap = {};
        scored.forEach(r => scoredMap[r.match_id] = r);

        const predMap = {};
        (predData.predictions || []).forEach(p => { predMap[p.match.id] = p; });

        const adviceMap = {};
        (adviceData.advice || []).forEach(a => { adviceMap[a.match_id] = a; });
        STATE.guessAdvice = adviceMap;
        STATE.guessData = savedMap;

        if (fixtures.length === 0) {
            container.innerHTML = `<div class="col-12 text-center py-4 text-muted">No fixtures for this gameweek</div>`;
            return;
        }

        const allFinished = fixtures.every(f => f.finished);
        const hasGuesses = saved.length > 0;

        updateGuessStatus(hasGuesses, allFinished, scoreData);

        container.innerHTML = fixtures.map(f => renderGuessCard(f, savedMap[f.id], scoredMap[f.id], predMap[f.id])).join("");

        attachGuessListeners();
    } catch (err) {
        container.innerHTML = `<div class="col-12 text-center py-4 text-danger">Error loading fixtures</div>`;
    }
}

async function loadBestBetsScore() {
    const container = $("#bestBetsScoreContainer");
    if (!container) return;
    const gw = STATE.selectedGW;
    try {
        const data = await api(`/api/guesses/${gw}/best-bets-score`);
        const rows = data.best_bets || [];
        const summary = data.summary || { total: 0, winner_ok: 0, score_ok: 0, ai_winner_ok: 0 };
        if (rows.length === 0) {
            container.innerHTML = "";
            return;
        }
        const th = "<tr><th>#</th><th>Match</th><th>Actual</th><th>Your pick</th><th>Winner</th><th>AI Advice</th><th>AI</th></tr>";
        function formatAiAdvice(aiAdvice) {
            if (!aiAdvice || aiAdvice === "–") return "–";
            const m = aiAdvice.match(/^(.+?)\s*\((\d+)-(\d+)\)\s*$/);
            if (m && m[2] === m[3]) return "Draw (" + m[2] + "-" + m[3] + ")";
            return aiAdvice;
        }
        let aiOkCount = 0;
        const body = rows.map(function(r) {
            const winnerCell = r.finished ? (r.winner_ok ? '<span class="text-success">✓</span>' : '<span class="text-danger">✗</span>') : "–";
            const aiAdviceDisplay = formatAiAdvice(r.ai_advice);
            const aiCorrect = r.finished ? (aiAdviceDisplay.startsWith("Draw") ? r.actual_winner === "Draw" : r.ai_winner_ok) : false;
            if (aiCorrect) aiOkCount++;
            const aiCell = r.finished ? (aiCorrect ? '<span class="text-success">✓</span>' : '<span class="text-danger">✗</span>') : "–";
            return "<tr>" +
                "<td>" + (r.rank || "") + "</td>" +
                "<td><strong>" + r.home_short + " v " + r.away_short + "</strong></td>" +
                "<td>" + r.actual_winner + " " + r.actual_score + "</td>" +
                "<td>" + r.user_winner + " " + r.user_score + "</td>" +
                "<td>" + winnerCell + "</td>" +
                "<td>" + aiAdviceDisplay + "</td>" +
                "<td>" + aiCell + "</td>" +
                "</tr>";
        }).join("");
        const sumRow = "<tr class=\"best-bets-summary\">" +
            "<td colspan=\"2\"><strong>Out of 5 Best Bets</strong></td>" +
            "<td colspan=\"2\"></td>" +
            "<td><strong>" + summary.winner_ok + "/5</strong></td>" +
            "<td></td>" +
            "<td><strong>" + aiOkCount + "/5</strong></td></tr>";
        container.innerHTML = "<div class=\"best-bets-score-card\">" +
            "<h5 class=\"mb-2\"><i class=\"bi bi-trophy me-2\"></i>How many you got right out of 5 Best Bets</h5>" +
            "<div class=\"table-responsive\"><table class=\"table table-sm best-bets-table\">" +
            "<thead>" + th + "</thead><tbody>" + body + sumRow + "</tbody></table></div></div>";
    } catch (err) {
        container.innerHTML = "";
    }
}

function updateGuessStatus(hasGuesses, allFinished, scoreData) {
    const text = $("#guessStatusText");
    const btn = $("#btnSaveGuesses");

    if (allFinished && scoreData.has_guesses) {
        text.innerHTML = `<i class="bi bi-check-circle-fill text-success me-1"></i>${STATE.gwLabel}${STATE.guessGW} finished — ` +
            `<b class="text-success">${scoreData.correct_winner || 0}</b> winner(s) correct, ` +
            `<b style="color:gold">${scoreData.correct_score || 0}</b> exact score(s) — ` +
            `<b class="text-success">${scoreData.points || 0} pts</b>`;
        btn.classList.add("d-none");
    } else if (hasGuesses) {
        text.innerHTML = `<i class="bi bi-pencil-fill me-1" style="color:var(--pl-green)"></i>Guesses saved. Edit and save again anytime.`;
        btn.classList.remove("d-none");
    } else {
        text.textContent = "Pick a winner or fill in exact scores for each match";
        btn.classList.remove("d-none");
    }
}

function renderGuessCard(match, saved, scored, predRow) {
    const m = match;
    const hasSaved = !!saved;
    const hasScored = scored && scored.scored;
    const pred = predRow ? predRow.prediction : null;
    const isTopBet = predRow && predRow.is_top_bet;
    const betRank = predRow && predRow.bet_rank;

    let resultClass = "";
    let badgeHtml = "";
    if (hasScored) {
        if (scored.score_correct) {
            resultClass = "result-correct";
            badgeHtml = `<span class="guess-result-badge exact"><i class="bi bi-star-fill"></i></span>`;
        } else if (scored.winner_correct) {
            resultClass = "result-correct";
            badgeHtml = `<span class="guess-result-badge correct"><i class="bi bi-check"></i></span>`;
        } else {
            resultClass = "result-wrong";
            badgeHtml = `<span class="guess-result-badge wrong"><i class="bi bi-x"></i></span>`;
        }
    } else if (isTopBet && betRank) {
        badgeHtml = `<span class="top-bet-badge">${betRank} Best Bet</span>`;
    }

    const selHome = saved && saved.winner === "home" ? "selected-home" : "";
    const selDraw = saved && saved.winner === "draw" ? "selected-draw" : "";
    const selAway = saved && saved.winner === "away" ? "selected-away" : "";
    const hs = saved && saved.home_score != null ? saved.home_score : "";
    const as_ = saved && saved.away_score != null ? saved.away_score : "";
    const disabled = (hasScored || match.is_live) ? "disabled" : "";

    const adviceData = STATE.guessAdvice[m.id];
    let adviceHtml = "";
    if (adviceData) {
        const labels = { home: m.home_short, draw: "Draw", away: m.away_short };
        const isDrawScore = adviceData.recommended_home_score === adviceData.recommended_away_score;
        const adviceWinnerLabel = isDrawScore ? "Draw" : labels[adviceData.recommended_winner];
        const reasonsJson = adviceData.reasons ? encodeURIComponent(JSON.stringify(adviceData.reasons)) : "";
        const matchTitle = `${m.home_short} vs ${m.away_short}`;
        adviceHtml = `<div class="d-flex align-items-center gap-2 mt-2">
            <div class="guess-advice-tag" style="flex:1">
                <i class="bi bi-lightbulb-fill"></i>
                Advice: ${adviceWinnerLabel} (${adviceData.recommended_home_score}-${adviceData.recommended_away_score})
            </div>
            <button class="btn-why" onclick="showReasoning('${matchTitle}', '${reasonsJson}')" title="Why this prediction?">
                <i class="bi bi-info-circle-fill"></i>
            </button>
        </div>`;
    }

    let actualHtml = "";
    if (hasScored) {
        actualHtml = `<div class="text-center mt-2" style="font-size:.75rem;color:var(--pl-text-muted)">
            Actual: <b style="color:var(--pl-text)">${scored.actual_home_score} - ${scored.actual_away_score}</b>
        </div>`;
    } else if (match.is_live) {
        const min = match.minutes || 0;
        const minText = min === 45 ? "HT" : min >= 90 ? "90+" : min + "'";
        const liveH = match.home_score != null ? match.home_score : 0;
        const liveA = match.away_score != null ? match.away_score : 0;
        actualHtml = `<div class="text-center mt-2" style="font-size:.8rem">
            <span class="live-match-tag" style="position:static"><span class="live-dot"></span>${minText}</span>
            <b style="color:var(--pl-text);margin-left:.4rem">${liveH} - ${liveA}</b>
        </div>`;
        resultClass = "match-live";
    }

    let predBlock = "";
    if (pred) {
        predBlock = `
            <div class="guess-pred-block mt-2 mb-2">
                <div class="pred-bar-wrap">
                    <div class="pred-bar-label small text-muted">AI</div>
                    <div class="pred-bar-container">
                        <div class="pred-bar-home" style="width:${pred.home_win_pct}%">${pred.home_win_pct}%</div>
                        <div class="pred-bar-draw" style="width:${pred.draw_pct}%">${pred.draw_pct}%</div>
                        <div class="pred-bar-away" style="width:${pred.away_win_pct}%">${pred.away_win_pct}%</div>
                    </div>
                </div>
                ${predRow.odds ? `
                <div class="pred-bar-wrap mt-1">
                    <div class="pred-bar-label small text-muted">${predRow.odds.source || "Market"}</div>
                    <div class="pred-bar-container pred-bar-odds">
                        <div class="pred-bar-home" style="width:${predRow.odds.home_pct}%">${predRow.odds.home_pct}%</div>
                        <div class="pred-bar-draw" style="width:${predRow.odds.draw_pct}%">${predRow.odds.draw_pct}%</div>
                        <div class="pred-bar-away" style="width:${predRow.odds.away_pct}%">${predRow.odds.away_pct}%</div>
                    </div>
                </div>` : ""}
                <div class="pred-score-line mt-1">
                    Predicted: <span>${pred.predicted_home_goals}</span> – <span>${pred.predicted_away_goals}</span>
                </div>
            </div>`;
    }

    return `
    <div class="col-12 col-sm-6 col-lg-4">
        <div class="guess-card ${hasSaved ? "has-guess" : ""} ${resultClass}" style="position:relative" data-match-id="${m.id}">
            ${badgeHtml}
            <div class="guess-teams">
                <div class="guess-team">
                    <img src="${m.home_badge}" alt="" loading="lazy" onerror="this.style.display='none'">
                    <span class="guess-team-name">${m.home_short}</span>
                </div>
                <span class="vs-badge" style="font-size:.7rem">VS</span>
                <div class="guess-team">
                    <img src="${m.away_badge}" alt="" loading="lazy" onerror="this.style.display='none'">
                    <span class="guess-team-name">${m.away_short}</span>
                </div>
            </div>
            ${predBlock}
            <div class="winner-btns">
                <button class="winner-btn ${selHome}" data-winner="home" data-mid="${m.id}" ${disabled}>${m.home_short}</button>
                <button class="winner-btn ${selDraw}" data-winner="draw" data-mid="${m.id}" ${disabled}>Draw</button>
                <button class="winner-btn ${selAway}" data-winner="away" data-mid="${m.id}" ${disabled}>${m.away_short}</button>
            </div>
            <div class="score-inputs">
                <input type="number" min="0" max="20" class="guess-score-home" data-mid="${m.id}"
                       value="${hs}" placeholder="–" ${disabled}>
                <span class="score-dash">–</span>
                <input type="number" min="0" max="20" class="guess-score-away" data-mid="${m.id}"
                       value="${as_}" placeholder="–" ${disabled}>
            </div>
            ${adviceHtml}
            ${actualHtml}
        </div>
    </div>`;
}

let _autoSaveTimer = null;

function triggerAutoSave() {
    clearTimeout(_autoSaveTimer);
    showAutoSaveStatus("unsaved");
    _autoSaveTimer = setTimeout(() => autoSaveGuesses(), 1500);
}

function showAutoSaveStatus(state) {
    const btn = $("#btnSaveGuesses");
    btn.classList.remove("d-none");
    if (state === "saving") {
        btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Saving...`;
        btn.disabled = true;
    } else if (state === "saved") {
        btn.innerHTML = `<i class="bi bi-check-circle-fill me-1"></i>Saved`;
        btn.disabled = false;
        btn.style.background = "var(--pl-green)";
        setTimeout(() => { btn.style.background = ""; }, 2000);
    } else if (state === "unsaved") {
        btn.innerHTML = `<i class="bi bi-circle-fill me-1" style="font-size:.5rem"></i>Unsaved changes...`;
        btn.disabled = false;
    } else if (state === "error") {
        btn.innerHTML = `<i class="bi bi-exclamation-triangle-fill me-1"></i>Retry`;
        btn.disabled = false;
        btn.style.background = "var(--pl-pink)";
        setTimeout(() => { btn.style.background = ""; }, 3000);
    }
}

async function autoSaveGuesses() {
    const guesses = Object.values(STATE.guessData).filter(g => g.winner);
    if (guesses.length === 0) return;

    showAutoSaveStatus("saving");
    try {
        await fetch(`/api/guesses/${STATE.guessGW}?league=${STATE.league}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ guesses }),
        });
        showAutoSaveStatus("saved");
        loadGuessHistory();
    } catch (err) {
        showAutoSaveStatus("error");
    }
}

function attachGuessListeners() {
    $$(".winner-btn:not([disabled])").forEach(btn => {
        btn.addEventListener("click", () => {
            const mid = parseInt(btn.dataset.mid);
            const winner = btn.dataset.winner;
            const card = btn.closest(".guess-card");

            card.querySelectorAll(".winner-btn").forEach(b => {
                b.classList.remove("selected-home", "selected-draw", "selected-away");
            });
            btn.classList.add(`selected-${winner}`);

            if (!STATE.guessData[mid]) STATE.guessData[mid] = { match_id: mid };
            STATE.guessData[mid].winner = winner;
            card.classList.add("has-guess");
            triggerAutoSave();
        });
    });

    $$(".guess-score-home:not([disabled]), .guess-score-away:not([disabled])").forEach(inp => {
        inp.addEventListener("input", () => {
            const mid = parseInt(inp.dataset.mid);
            const card = inp.closest(".guess-card");
            const homeInp = card.querySelector(".guess-score-home");
            const awayInp = card.querySelector(".guess-score-away");
            const hv = homeInp.value !== "" ? parseInt(homeInp.value) : null;
            const av = awayInp.value !== "" ? parseInt(awayInp.value) : null;

            if (!STATE.guessData[mid]) STATE.guessData[mid] = { match_id: mid };
            STATE.guessData[mid].home_score = hv;
            STATE.guessData[mid].away_score = av;

            if (hv !== null && av !== null) {
                let autoWinner;
                if (hv > av) autoWinner = "home";
                else if (hv === av) autoWinner = "draw";
                else autoWinner = "away";

                STATE.guessData[mid].winner = autoWinner;
                card.querySelectorAll(".winner-btn").forEach(b => {
                    b.classList.remove("selected-home", "selected-draw", "selected-away");
                });
                card.querySelector(`.winner-btn[data-winner="${autoWinner}"]`)
                    .classList.add(`selected-${autoWinner}`);
                card.classList.add("has-guess");
            }
            triggerAutoSave();
        });
    });
}

async function saveGuesses() {
    clearTimeout(_autoSaveTimer);
    await autoSaveGuesses();
}

async function loadAdvice() {
    const btn = $("#btnGetAdvice");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>`;
    try {
        await loadGuessCards();
    } catch (err) {
        console.error("Advice refresh error:", err);
    } finally {
        btn.disabled = false;
        btn.innerHTML = `<i class="bi bi-lightbulb-fill me-1"></i>Advice`;
    }
}

async function loadGuessHistory() {
    const container = $("#guessHistoryContainer");
    try {
        const data = await api("/api/guesses/history");
        const history = data.history || [];

        if (history.length === 0) {
            container.innerHTML = `<div class="text-center text-muted small py-3">No guesses yet. Start guessing!</div>`;
            return;
        }

        let totalPoints = 0;
        let totalCorrectW = 0;
        let totalCorrectS = 0;

        const cards = history.map(h => {
            const isPending = h.pending || h.total_matches === 0;
            totalPoints += h.points || 0;
            totalCorrectW += h.correct_winner || 0;
            totalCorrectS += h.correct_score || 0;

            return `<div class="history-card ${isPending ? "pending" : ""}" data-gw="${h.gameweek}">
                <span class="history-gw">${STATE.gwLabel}${h.gameweek}</span>
                <div class="history-stats">
                    <div class="history-stat">
                        <span class="stat-num">${isPending ? "–" : h.correct_winner}</span>
                        <span class="stat-label">Winners</span>
                    </div>
                    <div class="history-stat">
                        <span class="stat-num" style="color:gold">${isPending ? "–" : h.correct_score}</span>
                        <span class="stat-label">Exact</span>
                    </div>
                    <div class="history-stat">
                        <span class="stat-num">${isPending ? "–" : h.total_matches}</span>
                        <span class="stat-label">Matches</span>
                    </div>
                </div>
                <span class="history-points">${isPending ? "Pending" : h.points + " pts"}</span>
            </div>`;
        }).join("");

        container.innerHTML = cards + `
            <div class="history-total-bar">
                <span class="total-label">
                    <i class="bi bi-trophy-fill me-1"></i>Total: ${totalCorrectW} winners, ${totalCorrectS} exact
                </span>
                <span class="total-points">${totalPoints} pts</span>
            </div>`;

        $$(".history-card").forEach(card => {
            card.addEventListener("click", () => {
                const gw = parseInt(card.dataset.gw);
                selectGW(gw);
                window.scrollTo({ top: 0, behavior: "smooth" });
            });
        });
    } catch (err) {
        container.innerHTML = `<div class="text-center text-danger small py-3">Error loading history</div>`;
    }
}

// ── Reasoning Modal ──

function showReasoning(matchTitle, encodedReasons) {
    const reasons = JSON.parse(decodeURIComponent(encodedReasons));

    let existing = document.getElementById("reasoningModal");
    if (existing) existing.remove();

    const icons = [
        "bi-graph-up",
        "bi-bullseye",
        "bi-house-fill",
        "bi-percent",
        "bi-trophy-fill",
    ];

    const reasonsHtml = reasons.map((r, i) => `
        <div class="reason-row">
            <i class="bi ${icons[i] || "bi-check-circle"} reason-icon"></i>
            <span>${r}</span>
        </div>
    `).join("");

    const modal = document.createElement("div");
    modal.id = "reasoningModal";
    modal.className = "reasoning-overlay";
    modal.innerHTML = `
        <div class="reasoning-popup">
            <div class="reasoning-header">
                <h6 class="mb-0"><i class="bi bi-lightbulb-fill me-2" style="color:#f5a623"></i>${matchTitle}</h6>
                <button class="reasoning-close" onclick="closeReasoning()"><i class="bi bi-x-lg"></i></button>
            </div>
            <div class="reasoning-body">
                <p class="reasoning-subtitle">Why this prediction?</p>
                ${reasonsHtml}
            </div>
        </div>`;

    document.body.appendChild(modal);
    requestAnimationFrame(() => modal.classList.add("show"));

    modal.addEventListener("click", e => {
        if (e.target === modal) closeReasoning();
    });
}

function closeReasoning() {
    const modal = document.getElementById("reasoningModal");
    if (modal) {
        modal.classList.remove("show");
        setTimeout(() => modal.remove(), 300);
    }
}

// ── Live Polling ──

let _liveInterval = null;

function startLivePolling() {
    checkLiveStatus();
    _liveInterval = setInterval(checkLiveStatus, 3000);
}

async function checkLiveStatus() {
    try {
        const data = await api("/api/live-status");
        const badge = $("#liveBadge");
        if (data.has_live) {
            badge.classList.remove("d-none");
            refreshAllLive();
        } else {
            badge.classList.add("d-none");
        }
    } catch (e) { /* silent */ }
}

async function refreshAllLive() {
    const activeTab = document.querySelector(".tab-content.active");
    if (!activeTab) return;
    const id = activeTab.id;

    try {
        // Always refresh the cache
        _cache["fixtures"] = null;

        if (id === "tab-results") {
            await loadFixtures(STATE.selectedGW, true);
            standingsLoaded = false;
            await loadStandings();
        } else if (id === "tab-guess") {
            await loadGuessCards();
        }
    } catch(e) { /* silent */ }
}

// ── Guess Sub-tabs ──

function initGuessSubtabs() {
    $$(".guess-subtab").forEach(btn => {
        btn.addEventListener("click", () => {
            $$(".guess-subtab").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            const sub = btn.dataset.subtab;
            sessionStorage.setItem("plSub", sub);
            $("#guessPredictionsSection").classList.add("d-none");
            $("#guessFillSection").classList.add("d-none");
            $("#guessCompareSection").classList.add("d-none");
            if (sub === "predictions") {
                $("#guessPredictionsSection").classList.remove("d-none");
                loadGuessPredictions();
                loadBestBetsScore();
            } else if (sub === "fill") {
                $("#guessFillSection").classList.remove("d-none");
            } else {
                $("#guessCompareSection").classList.remove("d-none");
                loadGuessComparison();
            }
        });
    });
}

let guessPredictionsLoading = false;
async function loadGuessPredictions() {
    if (guessPredictionsLoading) return;
    guessPredictionsLoading = true;
    const container = $("#guessPredictionsContainer");
    const gw = STATE.selectedGW;
    container.innerHTML = `<div class="col-12 text-center py-4"><div class="spinner-border text-light spinner-border-sm"></div><p class="text-muted small mt-2">Loading predictions...</p></div>`;
    try {
        const [predResponse, adviceResponse] = await Promise.all([
            api(`/api/predictions/${gw}`),
            api(`/api/guess-advice/${gw}`).catch(() => ({ advice: [] })),
        ]);
        const adviceMap = {};
        (adviceResponse.advice || []).forEach(a => { adviceMap[a.match_id] = a; });
        STATE.guessAdvice = adviceMap;
        if (!predResponse.predictions || predResponse.predictions.length === 0) {
            container.innerHTML = `<div class="col-12 text-center py-4 text-muted">No fixtures for this gameweek</div>`;
            return;
        }
        container.innerHTML = predResponse.predictions.map(renderPredCard).join("");
    } catch (err) {
        container.innerHTML = `<div class="col-12 text-center py-4 text-danger">Error loading predictions</div>`;
    } finally {
        guessPredictionsLoading = false;
    }
}

async function loadGuessComparison() {
    const container = $("#comparisonContainer");
    container.innerHTML = `<div class="text-center py-4">
        <div class="spinner-border text-light spinner-border-sm"></div></div>`;

    try {
        const data = await api(`/api/comparison/${STATE.guessGW}`);
        const comps = data.comparisons || [];
        const us = data.user_score || { correct_winner: 0, correct_score: 0, points: 0 };
        const ai = data.ai_score || { correct_winner: 0, correct_score: 0, points: 0 };

        if (comps.length === 0) {
            const msg = data.error ? `Error: ${data.error}` : "No data for this gameweek";
            container.innerHTML = `<div class="text-center py-4 text-muted">${msg}</div>`;
            return;
        }

        const anyHasResult = comps.some(c => c.actual_winner != null);
        const anyFinished = comps.some(c => c.finished);

        let scoreboardHtml = "";
        if ((anyHasResult || anyFinished) && us && ai) {
            const userWins = us.points > ai.points;
            const aiWins = ai.points > us.points;
            scoreboardHtml = `
            <div class="cmp-scoreboard">
                <div class="cmp-score-card ${userWins ? "winner" : ""}">
                    <div class="cmp-who"><i class="bi bi-person-fill me-1"></i>You</div>
                    <div class="cmp-pts" style="color:${userWins ? "var(--pl-green)" : "var(--pl-text)"}">${us.points} pts</div>
                    <div class="cmp-detail">${us.correct_winner} winners / ${us.correct_score} exact</div>
                </div>
                <div class="cmp-score-card ${aiWins ? "winner" : ""}">
                    <div class="cmp-who"><i class="bi bi-cpu-fill me-1"></i>AI</div>
                    <div class="cmp-pts" style="color:${aiWins ? "var(--pl-cyan)" : "var(--pl-text)"}">${ai.points} pts</div>
                    <div class="cmp-detail">${ai.correct_winner} winners / ${ai.correct_score} exact</div>
                </div>
            </div>`;
        }

        const rows = comps.map(c => {
            const Wval = {home: c.home_short, draw: "Draw", away: c.away_short};
            const hasResult = c.actual_winner != null;
            const live = c.is_live;

            let actW = "–", actS = "–";
            if (hasResult) {
                actW = Wval[c.actual_winner];
                actS = (c.actual_home != null ? c.actual_home : 0) + "-" + (c.actual_away != null ? c.actual_away : 0);
                if (live) {
                    const min = c.minutes || 0;
                    actW = '<span style="color:var(--pl-pink)">' + (min === 45 ? "HT" : min >= 90 ? "90+" : min + "'") + '</span> ' + actW;
                }
            }

            const uW = c.user_winner ? Wval[c.user_winner] : "–";
            const uS = c.user_home != null ? c.user_home+"-"+c.user_away : "–";
            const aW = c.ai_winner ? Wval[c.ai_winner] : "–";
            const aS = c.ai_home != null ? c.ai_home+"-"+c.ai_away : "–";

            function cell(val, ok, hasPick) {
                if (!hasResult || !hasPick) return '<td class="cmp-cell">' + val + '</td>';
                if (ok) return '<td class="cmp-cell correct">' + val + ' ✓</td>';
                return '<td class="cmp-cell wrong">' + val + ' ✗</td>';
            }

            // Order: Match | Winner: Actual, You, AI | Score: Actual, You, AI (compare winner to winner, score to score)
            return '<tr' + (live?' style="background:rgba(233,0,82,.06)"':'') + '>' +
                '<td><div class="match-col"><img src="'+c.home_badge+'" onerror="this.style.display=\'none\'">'+c.home_short+' v '+c.away_short+'<img src="'+c.away_badge+'" onerror="this.style.display=\'none\'"></div></td>' +
                '<td class="cmp-cell actual">' + actW + '</td>' +
                cell(uW, c.u_w_ok, c.user_winner) +
                cell(aW, c.a_w_ok, c.ai_winner) +
                '<td class="cmp-cell actual">' + actS + '</td>' +
                cell(uS, c.u_s_ok, c.user_home != null) +
                cell(aS, c.a_s_ok, c.ai_home != null) +
                '</tr>';
        }).join("");

        var cardsHtml = comps.map(function(c) {
            var Wval = {home: c.home_short, draw: "Draw", away: c.away_short};
            var hasResult = c.actual_winner != null;
            var actW = "–", actS = "–";
            if (hasResult) {
                actW = Wval[c.actual_winner];
                actS = (c.actual_home != null ? c.actual_home : 0) + "-" + (c.actual_away != null ? c.actual_away : 0);
            }
            var uW = c.user_winner ? Wval[c.user_winner] : "–";
            var uS = c.user_home != null ? c.user_home+"-"+c.user_away : "–";
            var aW = c.ai_winner ? Wval[c.ai_winner] : "–";
            var aS = c.ai_home != null ? c.ai_home+"-"+c.ai_away : "–";
            var uWMark = (hasResult && c.user_winner) ? (c.u_w_ok ? " ✓" : " ✗") : "";
            var uSMark = (hasResult && c.user_home != null) ? (c.u_s_ok ? " ✓" : " ✗") : "";
            var aWMark = (hasResult && c.ai_winner) ? (c.a_w_ok ? " ✓" : " ✗") : "";
            var aSMark = (hasResult && c.ai_home != null) ? (c.a_s_ok ? " ✓" : " ✗") : "";
            return '<div class="cmp-card' + (c.is_live ? ' cmp-card-live' : '') + '">' +
                '<div class="cmp-card-match">' +
                '<img src="'+c.home_badge+'" onerror="this.style.display=\'none\'">' + c.home_short + ' v ' + c.away_short +
                '<img src="'+c.away_badge+'" onerror="this.style.display=\'none\'"></div>' +
                '<div class="cmp-card-block"><span class="cmp-block-title">Winner</span>' +
                '<div class="cmp-card-row"><span class="cmp-label">Actual</span> ' + actW + '</div>' +
                '<div class="cmp-card-row"><span class="cmp-label">You</span> ' + uW + uWMark + '</div>' +
                '<div class="cmp-card-row"><span class="cmp-label">AI</span> ' + aW + aWMark + '</div></div>' +
                '<div class="cmp-card-block"><span class="cmp-block-title">Score</span>' +
                '<div class="cmp-card-row"><span class="cmp-label">Actual</span> ' + actS + '</div>' +
                '<div class="cmp-card-row"><span class="cmp-label">You</span> ' + uS + uSMark + '</div>' +
                '<div class="cmp-card-row"><span class="cmp-label">AI</span> ' + aS + aSMark + '</div></div>' +
                '</div>';
        }).join("");

        container.innerHTML = `
            ${scoreboardHtml}
            <div class="cmp-cards d-md-none">${cardsHtml}</div>
            <div class="table-responsive d-none d-md-block">
                <table class="cmp-table">
                    <thead>
                        <tr>
                            <th>Match</th>
                            <th colspan="3">Winner</th>
                            <th colspan="3">Score</th>
                        </tr>
                        <tr class="cmp-subhead">
                            <th></th>
                            <th>Actual</th>
                            <th>You</th>
                            <th>AI</th>
                            <th>Actual</th>
                            <th>You</th>
                            <th>AI</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`;
    } catch (err) {
        container.innerHTML = `<div class="text-center text-danger py-3">Error loading comparison</div>`;
    }
}

async function pushUpdate() {
    const btn = document.getElementById("btnPushUpdate");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Updating...`;
    try {
        const resp = await fetch(`/api/push-update?league=${STATE.league}`, { method: "POST" });
        const data = await resp.json();
        if (data.ok) {
            btn.innerHTML = `<i class="bi bi-check-circle-fill me-1"></i>Updated!`;
            btn.style.background = "var(--pl-green)";
            setTimeout(() => { btn.innerHTML = `<i class="bi bi-phone me-1"></i>Push to Phone`; btn.style.background = "var(--pl-cyan)"; btn.disabled = false; }, 3000);
        } else {
            btn.innerHTML = `<i class="bi bi-x-circle me-1"></i>Failed`;
            btn.style.background = "var(--pl-pink)"; btn.style.color = "#fff";
            setTimeout(() => { btn.innerHTML = `<i class="bi bi-phone me-1"></i>Push to Phone`; btn.style.background = "var(--pl-cyan)"; btn.style.color = ""; btn.disabled = false; }, 3000);
        }
    } catch (e) {
        btn.innerHTML = `<i class="bi bi-phone me-1"></i>Push to Phone`;
        btn.disabled = false;
    }
}

async function importPhoneGuesses() {
    const code = prompt("Paste the sync code from your phone:");
    if (!code || !code.trim()) return;
    try {
        const json = decodeURIComponent(escape(atob(code.trim())));
        const phoneData = JSON.parse(json);
        const resp = await fetch(`/api/import-phone-guesses?league=${STATE.league}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ guesses: phoneData }),
        });
        const result = await resp.json();
        if (result.ok) {
            alert(`Imported ${result.imported} new guesses from phone!`);
            loadGuessCards();
            loadGuessHistory();
        } else {
            alert("Error: " + (result.error || "Unknown"));
        }
    } catch (e) {
        alert("Invalid code. Make sure you copied the full code from the phone.");
    }
}

document.addEventListener("DOMContentLoaded", init);
