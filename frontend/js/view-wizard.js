/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* First-run setup: choose features, install their packages, done. Also Settings → Features → Run setup again. */
"use strict";

(function () {
    const h = SH.h;
    const C = SH.controls;
    let shownThisSession = false;

    const STATE_TEXT = {
        ready: ["on", "Ready"],
        needs_install: ["info", "Needs packages"],
        needs_setup: ["warn", "Needs setup"],
        impossible: ["", "Not possible here"],
    };

    function open() {
        // what each feature would be if it were on (an "off" feature still has a real answer in the dashboard)
        let step = 1;
        const choices = {};
        const body = h("div");
        const back = C.button("Back", () => { step -= 1; draw(); }, "secondary");
        const skip = C.button("Use defaults", () => finish({}), "link");
        const next = C.button("Next", () => go(), "primary");
        const m = C.modal({ title: "Set up ShareHub", body, actions: [skip, back, next], size: "lg", persistent: true });
        let details = null;
        let installedFiles = false;

        async function loadDetails() {
            if (details) return details;
            body.replaceChildren(h("p", { class: "muted pad" }, "Checking this system…"));
            const data = await SH.call("health");
            details = Object.fromEntries(data.features.map(f => [f.id, f]));
            return details;
        }

        // state as if the feature were on: the dashboard knows it even for features turned off
        function stateOf(id) {
            const d = details && details[id];
            if (!d) return SH.feature(id);
            const st = d.status === "unsupported" || d.status === "unavailable" ? "impossible"
                : d.setup_needed ? "needs_setup"
                    : d.installable.length && !d.packages.every(g => g.satisfied) ? "needs_install" : "ready";
            return { state: st, reason: d.reason, install: st === "needs_install" ? d.installable : [] };
        }

        function stepsBar() {
            return h("div", { class: "wizard-steps" }, ["1. Features", "2. Packages", "3. Done"].map((t, i) => h("span", { class: step === i + 1 ? "on" : "" }, t)));
        }

        function featureStep() {
            const st = SH.store.status;
            const items = SH.FEATURES.map(f => {
                const s = stateOf(f.id);
                const impossible = s.state === "impossible";
                if (!(f.id in choices)) choices[f.id] = !impossible && (st.state.features.configured ? SH.featureOn(f.id) : !f.optIn && f.id !== "netbios" && f.id !== "ad" && f.id !== "versions");
                const [kind, text] = STATE_TEXT[s.state] || STATE_TEXT.ready;
                const cb = C.checkbox({ label: f.label, checked: choices[f.id], description: impossible ? s.reason : f.about, disabled: impossible,
                    onChange: v => { choices[f.id] = v; } });
                return h("div", { class: "wizard-item" + (impossible ? " is-impossible" : "") }, cb.el, C.badge(text, kind));
            });
            return h("div", { class: "form" }, stepsBar(),
                h("p", {}, "Choose what this server should do. SMB file sharing is always on. You can change this later under Settings → Features."),
                h("div", { class: "wizard-list" }, items));
        }

        function packagesFor() {
            const pkgs = [];
            SH.FEATURES.forEach(f => {
                if (!choices[f.id]) return;
                const s = stateOf(f.id);
                if (s.state === "needs_install") s.install.forEach(p => { if (!pkgs.includes(p)) pkgs.push(p); });
            });
            return pkgs;
        }

        function packagesStep() {
            const pkgs = packagesFor();
            const setup = SH.FEATURES.filter(f => choices[f.id] && stateOf(f.id).state === "needs_setup");
            return h("div", { class: "form" }, stepsBar(),
                pkgs.length
                    ? h("div", {}, h("p", {}, "These packages will be installed:"), h("ul", { class: "pkg-list mono" }, pkgs.map(p => h("li", {}, p))),
                        h("p", { class: "muted small" }, "apt-get install runs on this server; it can take a few minutes."))
                    : h("p", {}, "Nothing to install: every chosen feature already has its packages."),
                pkgs.includes("cockpit-files") ? C.alert("info", "Cockpit Files comes from backports.", "ShareHub enables backports the way Cockpit's install guide does and takes only what Cockpit Files needs from there; this may update Cockpit itself, and the page may reconnect.") : null,
                setup.length ? C.alert("warning", "Some features need the storage prepared first.",
                    h("ul", { class: "pkg-list" }, setup.map(f => h("li", {}, h("strong", {}, f.label + ": "), stateOf(f.id).reason || "")))) : null);
        }

        async function go() {
            if (step === 1) { step = 2; draw(); return; }
            if (step === 2) {
                const pkgs = packagesFor();
                m.setError("");
                m.setBusy(true);
                try {
                    if (pkgs.length) {
                        const text = h("p", {}, `Installing ${pkgs.join(" ")}… Keep this page open.`);
                        body.replaceChildren(stepsBar(), h("div", { class: "busy-box" }, h("span", { class: "spinner", "aria-hidden": "true" }), text));
                        // packages from backports (Cockpit Files) install on their own, in the background
                        const later = pkgs.filter(p => p === "cockpit-files");
                        const now = pkgs.filter(p => !later.includes(p));
                        if (now.length) await SH.call("install-packages", { packages: now });
                        if (later.length) await SH.runInstall(later, step => { text.textContent = step; });
                    }
                    if (pkgs.includes("cockpit-files")) installedFiles = true;
                    await finish(choices, true);
                } catch (e) {
                    m.setBusy(false);
                    draw();
                    m.setError(e.message);
                }
                return;
            }
            m.close();
        }

        async function finish(choice, stay) {
            try {
                const data = await SH.call("features-set", { features: choice });
                await SH.refresh();
                if (!stay) { m.close(); SH.report(data, "Setup saved."); return; }
                step = 3;
                m.setBusy(false);
                draw(data);
            } catch (e) { m.setBusy(false); m.setError(e.message); }
        }

        function doneStep(data) {
            const rows = SH.FEATURES.map(f => {
                const s = SH.feature(f.id);
                const [kind, text] = s.state === "off" ? ["off", "Off"] : STATE_TEXT[s.state] || STATE_TEXT.ready;
                return h("tr", {}, h("td", {}, f.label), h("td", {}, C.badge(text, kind)));
            });
            const warnings = ((data && data.warnings) || []).filter(w => !/^systemd is not running/.test(w));
            return h("div", { class: "form" }, stepsBar(),
                h("p", {}, "ShareHub is ready. Next: create users and your first share."),
                installedFiles ? C.alert("info", "Reload Cockpit to see Cockpit Files.", h("span", {}, "The \"File browser\" menu entry appears after a reload. ",
                    C.button("Reload now", () => window.top.location.reload(), "link"))) : null,
                warnings.length ? C.alert("warning", "Notes", warnings.join(" ")) : null,
                h("table", { class: "tbl tbl-compact" }, h("tbody", {}, rows)),
                h("p", { class: "muted small" }, "Overview → Components shows every dependency in detail."));
        }

        function draw(data) {
            back.classList.toggle("hidden", step !== 2);
            skip.classList.toggle("hidden", step !== 1);
            next.textContent = step === 1 ? "Next" : step === 2 ? (packagesFor().length ? "Install and finish" : "Finish") : "Close";
            body.replaceChildren(step === 1 ? featureStep() : step === 2 ? packagesStep() : doneStep(data));
        }

        loadDetails().then(() => draw()).catch(e => { body.replaceChildren(C.alert("danger", "Could not check this system.", e.message)); });
    }

    SH.views.wizard = {
        open,
        /* Fresh install: not configured yet and no shares - offer the setup once per session. */
        maybeOpen(st) {
            if (shownThisSession || st.state.features.configured || Object.keys(st.state.shares).length) return;
            shownThisSession = true;
            open();
        },
    };
})();
