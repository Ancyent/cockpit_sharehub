/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* Versions & restore: browse read-only snapshots and the protected recycle bin of a share,
 * one folder and one page at a time, so shares with millions of files stay fast.
 * Folder restores run as background jobs (SH.views.jobs). */
"use strict";

(function () {
    const h = SH.h;
    const C = SH.controls;
    const PAGE = 500;

    const fmtTime = iso => new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    const fmtEpoch = s => new Date(s * 1000).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

    /* ---------------------------------------------------------------- background jobs */

    /* Running, or finished in the last 15 minutes. */
    const recentJob = j => j.state === "running" || j.state === "queued" || (j.finished && Date.now() - new Date(j.finished).getTime() < 15 * 60000);

    const JOB_STATE = { queued: ["", "Starting"], running: ["info", "Restoring"], done: ["on", "Done"], failed: ["danger", "Failed"], cancelled: ["warn", "Cancelled"] };

    function jobRow(job) {
        const [kind, text] = JOB_STATE[job.state] || ["", job.state];
        const counting = job.state === "running" && job.total_files === null;
        const pct = job.total_files ? Math.min(100, Math.round((job.files / job.total_files) * 100)) : 0;
        const fill = h("div", { class: "progress-fill" });
        fill.style.width = (job.state === "done" ? 100 : pct) + "%";
        const bar = h("div", { class: "progress" + (counting || job.state === "queued" ? " indeterminate" : ""), role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct), "aria-label": `Restore of ${job.label}` }, fill);
        const detail = counting ? "Counting files…"
            : job.total_files !== null ? `${job.files.toLocaleString()} of ${job.total_files.toLocaleString()} files · ${SH.bytes(job.bytes)} of ${SH.bytes(job.total_bytes)}` : "";
        let result = null;
        if (job.state === "done") result = job.kind === "restore-snapshot" ? `Restored as "${job.result}".` : `${SH.plural(job.result || 0, "file")} put back.`;
        else if (job.state === "failed") result = job.error;
        else if (job.state === "cancelled") result = job.kind === "restore-snapshot" && job.result ? `Stopped. The files copied so far are in "${job.result}".` : "Stopped. Files already restored stay restored.";
        const active = job.state === "running" || job.state === "queued";
        return h("div", { class: "job" },
            h("div", { class: "job-head" },
                h("div", {}, h("span", { class: "strong" }, job.label), h("span", { class: "muted small" }, ` · ${job.share} · ${job.kind === "restore-snapshot" ? "from a version" : "from deleted files"}`)),
                h("div", { class: "badges" }, C.badge(text, kind),
                    active ? C.button("Cancel", async () => {
                        try { await SH.call("job-cancel", { id: job.id }); } catch (e) { SH.toast(e.message, "danger"); }
                    }, "link", { class: "btn btn-link btn-sm btn-danger-link" }) : null)),
            active || job.state === "done" ? bar : null,
            h("div", { class: "vb-status" }, active ? detail : result || ""));
    }

    /* A self-updating list of restore jobs; polls only while visible and busy. With a share, it
     * shows only jobs that are running or finished while this view was open. */
    function jobsBox(share, onFinished) {
        const finishedHere = new Set();
        const box = h("div", { class: "jobs", "aria-live": "polite" });
        const seenRunning = new Set();
        let timer = null;
        async function poll() {
            if (!box.isConnected) { clearTimeout(timer); return; }
            let list = [];
            try { list = (await SH.call("jobs")).jobs; } catch (e) { /* keep the last view */ }
            if (!share) list = list.filter(recentJob);
            if (share) list = list.filter(j => j.share === share && (j.state === "running" || j.state === "queued" || finishedHere.has(j.id) || seenRunning.has(j.id)));
            box.replaceChildren(...list.slice(0, 5).map(jobRow));
            box.classList.toggle("hidden", !list.length);
            let busy = false;
            list.forEach(j => {
                if (j.state === "running" || j.state === "queued") { busy = true; seenRunning.add(j.id); }
                else if (seenRunning.has(j.id)) { seenRunning.delete(j.id); finishedHere.add(j.id); if (onFinished) onFinished(j); }
            });
            clearTimeout(timer);
            if (busy) timer = setTimeout(poll, 1500);
        }
        box.classList.add("hidden");
        setTimeout(poll, 0);
        return { el: box, poll };
    }

    SH.views.jobs = { box: () => jobsBox(null, () => SH.refresh().catch(() => {})).el, recent: recentJob };

    /* ---------------------------------------------------------------- folder browser (versions + recycle bin) */

    /* opts: {name, mode: "version"|"recycle", snapshot(), onRestore(payload, label), history(path)} */
    function browser(opts) {
        const recycle = opts.mode === "recycle";
        let path = "";
        let query = "";
        let entries = [];
        let total = 0;
        let truncated = false;
        let loading = false;
        let searchResult = null;
        let token = 0;

        const crumbs = h("div", { class: "crumbs" });
        const filter = h("input", { type: "text", class: "input search", placeholder: "Filter this folder…", "aria-label": "Filter this folder by name" });
        const searchBtn = C.button("Search subfolders", () => runSearch(), "secondary", { class: "btn btn-secondary btn-sm", title: "Look for the name in this folder and every folder below it" });
        const status = h("div", { class: "vb-status", "aria-live": "polite" });
        const body = h("tbody");
        const more = h("div", { class: "vb-more" });
        const scroll = h("div", { class: "vb-scroll" },
            h("table", { class: "tbl tbl-compact" },
                h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Size"), h("th", {}, recycle ? "Deleted" : "Modified"), h("th", { class: "col-actions" }, h("span", { class: "sr-only" }, "Actions")))),
                body),
            more);

        // load the next page automatically when the end of the list scrolls into view
        const sentinel = h("div", { class: "vb-sentinel" });
        more.appendChild(sentinel);
        if ("IntersectionObserver" in window) {
            new IntersectionObserver(items => {
                if (items.some(i => i.isIntersecting) && !loading && !searchResult && entries.length < total) load(false);
            }, { root: scroll }).observe(sentinel);
        }

        let debounce = null;
        filter.addEventListener("input", () => {
            clearTimeout(debounce);
            debounce = setTimeout(() => { query = filter.value.trim(); searchResult = null; load(true); }, 300);
        });
        filter.addEventListener("keydown", ev => { if (ev.key === "Enter") { ev.preventDefault(); runSearch(); } });

        function request(extra) {
            const base = { name: opts.name, path };
            if (!recycle) base.snapshot = opts.snapshot();
            return Object.assign(base, extra);
        }

        function drawCrumbs() {
            const parts = path ? path.split("/") : [];
            crumbs.replaceChildren(
                C.button(recycle ? "Deleted files" : opts.name, () => openFolder(""), "link"),
                ...parts.map((p, i) => [h("span", { class: "muted" }, " / "), C.button(p, () => openFolder(parts.slice(0, i + 1).join("/")), "link")]).flat());
        }

        function openFolder(rel) {
            path = rel;
            query = "";
            filter.value = "";
            searchResult = null;
            load(true);
        }

        function row(e) {
            const depth = e.path.split("/").length;
            const isUserFolder = recycle && depth === 1;
            const name = e.type === "dir"
                ? C.button("📁 " + e.name, () => openFolder(e.path), "link", { "aria-label": `Open folder ${e.name}` })
                : h("span", {}, e.original || e.name, e.copy ? C.badge(`older copy #${e.copy}`) : null);
            const where = searchResult && e.path.includes("/") ? h("div", { class: "muted small mono" }, e.path.slice(0, e.path.lastIndexOf("/"))) : null;
            const actions = [];
            if (!isUserFolder) {
                actions.push(C.button("Restore", () => opts.onRestore(e), "link", { "aria-label": `Restore ${e.name}` }));
            }
            if (!recycle && e.type === "file") actions.push(C.button("History", () => opts.history(e.path), "link", { "aria-label": `History of ${e.name}` }));
            return h("tr", {},
                h("td", {}, isUserFolder ? h("span", {}, C.button("👤 " + e.name, () => openFolder(e.path), "link"), h("span", { class: "muted small" }, " deleted these files")) : name, where),
                h("td", { class: "small" }, e.type === "dir" ? "-" : SH.bytes(e.size)),
                h("td", { class: "small muted" }, fmtEpoch(e.mtime)),
                h("td", { class: "col-actions" }, actions));
        }

        function draw() {
            const list = searchResult ? searchResult.results : entries;
            body.replaceChildren(...list.map(row));
            if (!list.length) body.appendChild(h("tr", {}, h("td", { colspan: "4", class: "muted center" },
                searchResult ? "Nothing found." : query ? "No name in this folder matches the filter." : recycle && !path ? "No deleted files are kept right now." : "This folder is empty.")));
            more.replaceChildren(sentinel);
            if (searchResult) {
                const why = searchResult.stopped === "time" ? " The search stopped after 15 seconds - open a deeper folder to search less."
                    : searchResult.stopped === "limit" ? ` Showing the first ${searchResult.results.length} matches - type more of the name.` : "";
                status.textContent = `${SH.plural(searchResult.results.length, "match")} for "${searchResult.query}" below this folder (${searchResult.scanned.toLocaleString()} names checked).${why}`;
                more.appendChild(C.button("Back to the folder", () => { searchResult = null; draw(); }, "link"));
                return;
            }
            status.textContent = total ? `${total.toLocaleString()} item${total === 1 ? "" : "s"}${query ? ` matching "${query}"` : ""}` +
                (entries.length < total ? ` · showing ${entries.length.toLocaleString()}` : "") +
                (truncated ? " · the listing stopped after 10 seconds; use the filter to narrow it down" : "") : "";
            if (entries.length < total) more.appendChild(C.button(`Show ${Math.min(PAGE, total - entries.length).toLocaleString()} more`, () => load(false), "link"));
        }

        async function load(reset) {
            if (!recycle && !opts.snapshot()) return;
            const my = ++token;
            loading = true;
            if (reset) { entries = []; total = 0; body.replaceChildren(h("tr", {}, h("td", { colspan: "4", class: "muted center" }, "Loading…"))); drawCrumbs(); }
            try {
                const data = await SH.call("versions-browse", request({ offset: entries.length, limit: PAGE, query }));
                if (my !== token) return;
                entries = entries.concat(data.entries);
                total = data.total;
                truncated = data.truncated;
                draw();
            } catch (e) {
                if (my !== token) return;
                body.replaceChildren(h("tr", {}, h("td", { colspan: "4" }, C.alert(recycle ? "info" : "danger", recycle ? "Nothing to show." : "Could not open this folder.", e.message))));
            } finally {
                if (my === token) loading = false;
            }
        }

        async function runSearch() {
            clearTimeout(debounce);
            const q = filter.value.trim();
            if (q.length < 2) { status.textContent = "Type at least 2 characters to search."; filter.focus(); return; }
            const my = ++token;
            body.replaceChildren(h("tr", {}, h("td", { colspan: "4", class: "muted center" }, h("span", { class: "spinner", "aria-hidden": "true" }), ` Searching for "${q}"… (at most 15 seconds)`)));
            more.replaceChildren(sentinel);
            try {
                const data = await SH.call("versions-search", request({ query: q }));
                if (my !== token) return;
                searchResult = data;
                draw();
            } catch (e) { if (my === token) status.textContent = e.message; }
        }

        drawCrumbs();
        return {
            el: h("div", { class: "form" }, h("div", { class: "vb-toolbar" }, filter, searchBtn), crumbs, status, scroll),
            reload: () => load(true),
            reset: () => openFolder(""),
        };
    }

    /* ---------------------------------------------------------------- restore actions */

    async function restore(name, payload, label, jobs, after) {
        try {
            const data = await SH.call("versions-restore", Object.assign({ name }, payload));
            if (data.job) {
                SH.toast(`Restoring ${label} in the background. You can close this window.`);
                jobs.poll();
            } else {
                SH.toast(`Restored ${label} as "${data.restored}".`);
                if (after) after();
            }
        } catch (e) { SH.toast(e.message, "danger"); }
    }

    function historyDialog(name, rel, jobs) {
        const box = h("div", {}, h("p", { class: "muted pad" }, "Looking through the versions…"));
        const close = C.button("Close", () => m.close(), "secondary");
        const m = C.modal({ title: `Versions of ${rel.split("/").pop()}`, body: box, actions: [close], size: "md" });
        SH.call("versions-history", { name, path: rel }).then(data => {
            if (!data.versions.length) { box.replaceChildren(C.empty("This file is not in any version.")); return; }
            box.replaceChildren(
                h("p", { class: "muted small" }, h("span", { class: "mono" }, rel), " - only versions where the file changed are listed."),
                h("table", { class: "tbl tbl-compact" },
                    h("thead", {}, h("tr", {}, h("th", {}, "Version"), h("th", {}, "Size"), h("th", {}, "File modified"), h("th", { class: "col-actions" }, h("span", { class: "sr-only" }, "Actions")))),
                    h("tbody", {}, data.versions.map(v => h("tr", {},
                        h("td", {}, v.current ? C.badge("Current file", "on") : fmtTime(v.time)),
                        h("td", { class: "small" }, SH.bytes(v.size)),
                        h("td", { class: "small muted" }, fmtEpoch(v.mtime)),
                        h("td", { class: "col-actions" }, v.current ? null : C.button("Restore", async () => {
                            m.close();
                            await restore(name, { snapshot: v.snapshot, path: rel }, rel.split("/").pop(), jobs);
                        }, "link")))))));
        }).catch(e => box.replaceChildren(C.alert("danger", "Could not read the versions.", e.message)));
    }

    function snapshotsPanel(name, info, jobs) {
        const box = h("div", { class: "form" });
        if (!info.versioning.snapshots && !info.snapshots.length) {
            box.append(C.empty("Scheduled versions are off for this share. Turn them on in Edit → Versions (needs ZFS or Btrfs)."));
            return box;
        }
        let snapshot = info.snapshots.length ? info.snapshots[0].name : null;
        const timeOf = n => fmtTime(info.snapshots.find(s => s.name === n).time);
        const files = browser({
            name, mode: "version", snapshot: () => snapshot,
            onRestore: async e => {
                const ok = await C.confirm({ title: `Restore ${e.name}`, message: [
                    `Copy "${e.path}" from the version of ${timeOf(snapshot)} back into the share.`,
                    "If it still exists, the current one is kept and the restored copy gets the version's date in its name.",
                    e.type === "dir" ? "Folders are restored in the background at low priority; you can follow the progress here or on the Overview." : null].filter(Boolean),
                confirmLabel: "Restore" });
                if (ok) await restore(name, { snapshot, path: e.path }, e.name, jobs);
            },
            history: rel => historyDialog(name, rel, jobs),
        });
        const picker = C.select({
            label: "Version", searchable: info.snapshots.length > 10, value: snapshot,
            options: info.snapshots.map((s, i) => ({ value: s.name, label: fmtTime(s.time) + (i === 0 ? "  (latest)" : "") })),
            onChange: v => { snapshot = v; files.reset(); },
        });
        const now = C.button("Create a version now", async () => {
            try {
                await SH.call("versions-create", { name });
                SH.toast("Version created.");
                const fresh = await SH.call("versions-list", { name });
                info.snapshots = fresh.snapshots;
                snapshot = info.snapshots[0].name;
                picker.setOptions(info.snapshots.map((s, i) => ({ value: s.name, label: fmtTime(s.time) + (i === 0 ? "  (latest)" : "") })));
                picker.set(snapshot);
                files.reset();
            } catch (e) { SH.toast(e.message, "danger"); }
        }, "secondary");
        box.append(h("div", { class: "grid-2 align-end" }, picker.el, h("div", {}, now)),
            h("p", { class: "muted small" }, `${SH.plural(info.snapshots.length, "version")} kept. Versions are read-only: nobody can change them from SMB, NFS or FTP.`),
            snapshot ? files.el : C.empty("No versions yet. The first one is taken within 15 minutes, or create one now."));
        if (snapshot) files.reload();
        return box;
    }

    function recyclePanel(name, info, jobs) {
        if (!info.recycle_enabled && !info.recycle_exists) return C.empty("The recycle bin is off for this share. Turn it on in Edit → SMB.");
        let files = null;
        files = browser({
            name, mode: "recycle",
            onRestore: async e => {
                if (e.type === "dir") {
                    const ok = await C.confirm({ title: `Restore folder ${e.name}`, message: [
                        `Put every deleted file inside "${e.path.split("/").slice(1).join("/")}" back where it was.`,
                        "Existing files are never overwritten. This runs in the background at low priority."], confirmLabel: "Restore folder" });
                    if (!ok) return;
                }
                await restore(name, { path: e.path }, e.original || e.name, jobs, () => files.reload());
            },
        });
        files.reload();
        return h("div", { class: "form" },
            h("p", { class: "muted small" }, "Files deleted over SMB, by the user who deleted them. Restoring puts a file back where it was; an existing file with the same name is kept."),
            files.el);
    }

    SH.views.versions = {
        async open(name, tab) {
            let info;
            try { info = await SH.call("versions-list", { name }); } catch (e) { SH.toast(e.message, "danger"); return; }
            const panel = h("div", { class: "tab-panel" });
            let recyclePanelEl = null;
            const jobs = jobsBox(name, () => { if (recyclePanelEl && current === "recycle") draw(); });
            const items = [{ id: "versions", label: "Versions", badge: String(info.snapshots.length) }, { id: "recycle", label: "Deleted files" }];
            let current = tab || (info.versioning.snapshots || info.snapshots.length ? "versions" : "recycle");
            const close = C.button("Close", () => m.close(), "secondary");
            const tabs = C.tabs({ items, active: current, small: true, onChange: id => { current = id; draw(); } });
            const m = C.modal({ title: `Versions of ${name}`, body: h("div", {}, tabs.el, jobs.el, panel), actions: [close], size: "lg" });
            function draw() {
                recyclePanelEl = current === "recycle" ? recyclePanel(name, info, jobs) : null;
                panel.replaceChildren(current === "versions" ? snapshotsPanel(name, info, jobs) : recyclePanelEl);
            }
            draw();
            return m;
        },
    };
})();
