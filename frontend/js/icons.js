/* SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 cockpit_sharehub contributors */
/* Simple original line icons (no third-party artwork). Built with the DOM so they work under Cockpit's CSP. */
"use strict";

(function () {
    const NS = "http://www.w3.org/2000/svg";

    function svg(shapes, size) {
        const el = document.createElementNS(NS, "svg");
        el.setAttribute("viewBox", "0 0 48 48");
        el.setAttribute("width", size || 28);
        el.setAttribute("height", size || 28);
        el.setAttribute("fill", "none");
        el.setAttribute("stroke", "currentColor");
        el.setAttribute("stroke-width", "2");
        el.setAttribute("stroke-linecap", "round");
        el.setAttribute("stroke-linejoin", "round");
        for (const [tag, attrs] of shapes) {
            const s = document.createElementNS(NS, tag);
            for (const [k, v] of Object.entries(attrs)) s.setAttribute(k, v);
            el.appendChild(s);
        }
        return el;
    }

    /* Device types macOS can show for an SMB server. These are generic drawings of the device
     * category, not Apple's icons; Finder shows its own artwork for the chosen model. */
    SH.icons = {
        server: size => svg([
            ["rect", { x: 10, y: 6, width: 28, height: 36, rx: 3 }],
            ["line", { x1: 15, y1: 14, x2: 33, y2: 14 }],
            ["line", { x1: 15, y1: 20, x2: 33, y2: 20 }],
            ["line", { x1: 15, y1: 26, x2: 33, y2: 26 }],
            ["circle", { cx: 24, cy: 35, r: 2 }],
        ], size),
        rack1u: size => svg([
            ["rect", { x: 4, y: 18, width: 40, height: 12, rx: 2 }],
            ["line", { x1: 9, y1: 24, x2: 21, y2: 24 }],
            ["line", { x1: 24, y1: 21, x2: 24, y2: 27 }],
            ["line", { x1: 28, y1: 21, x2: 28, y2: 27 }],
            ["line", { x1: 32, y1: 21, x2: 32, y2: 27 }],
            ["circle", { cx: 39, cy: 24, r: 1.5 }],
        ], size),
        rack: size => svg([
            ["rect", { x: 9, y: 4, width: 30, height: 40, rx: 2 }],
            ["rect", { x: 13, y: 9, width: 22, height: 6, rx: 1 }],
            ["rect", { x: 13, y: 18, width: 22, height: 6, rx: 1 }],
            ["rect", { x: 13, y: 27, width: 22, height: 6, rx: 1 }],
            ["line", { x1: 13, y1: 39, x2: 35, y2: 39 }],
        ], size),
        backup: size => svg([
            ["rect", { x: 6, y: 26, width: 36, height: 14, rx: 3 }],
            ["circle", { cx: 36, cy: 33, r: 1.5 }],
            ["path", { d: "M15 18a9 9 0 1 1 3 6.7" }],
            ["polyline", { points: "12 15 15 18 18 15" }],
            ["polyline", { points: "24 12 24 17 27 19" }],
        ], size),
        tower: size => svg([
            ["rect", { x: 13, y: 4, width: 22, height: 40, rx: 3 }],
            ["circle", { cx: 20, cy: 12, r: 1.5 }],
            ["circle", { cx: 28, cy: 12, r: 1.5 }],
            ["circle", { cx: 20, cy: 18, r: 1.5 }],
            ["circle", { cx: 28, cy: 18, r: 1.5 }],
            ["line", { x1: 18, y1: 36, x2: 30, y2: 36 }],
        ], size),
        desktop: size => svg([
            ["rect", { x: 5, y: 7, width: 38, height: 26, rx: 2 }],
            ["line", { x1: 24, y1: 33, x2: 24, y2: 40 }],
            ["line", { x1: 16, y1: 41, x2: 32, y2: 41 }],
        ], size),
    };
})();
