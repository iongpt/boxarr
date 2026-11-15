/*
 * A minimal browser for running Boxarr's page scripts under node.
 *
 * Loads the real static scripts (app.js, update-result.js, ...) and the inline
 * <script> of a rendered page into one vm context with a small fake DOM, so a
 * test can call the page's own functions - initRegionPicker(),
 * saveConfiguration(), regenerateWeek(), showUpdateOutcome() - and check what
 * the user would see: alerts, toasts (showMessage logs "[type] message"),
 * element text, and the bodies posted with fetch.
 *
 * The DOM is deliberately small. A selector it does not understand throws
 * instead of matching nothing, so a test cannot pass by finding no elements.
 *
 * Run as a script (tests/js_sandbox.py does): node browser_sandbox.js PAYLOAD
 * where PAYLOAD is a JSON file {driver, data}. The driver is the body of an
 * async function (data, createSandbox); what it returns is printed as JSON.
 *
 * Keep it runnable on Node 14: no ??=, .at(), replaceAll or structuredClone.
 */
'use strict';

const fs = require('fs');
const vm = require('vm');

function camel(name) {
    return name.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}

// ---------------------------------------------------------------- selectors

const SIMPLE = /^(?:(\*|[a-zA-Z][\w-]*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:([\^$*]?=)(?:"([^"]*)"|'([^']*)'|([^\]"']*)))?\]|:(checked|disabled))/;

function splitOutside(text, separators) {
    // Split on any of `separators` outside [...] and quotes.
    const parts = [];
    let depth = 0;
    let quote = null;
    let current = '';
    for (const ch of text) {
        if (quote) {
            if (ch === quote) quote = null;
        } else if (ch === '"' || ch === "'") {
            quote = ch;
        } else if (ch === '[') {
            depth += 1;
        } else if (ch === ']') {
            depth -= 1;
        } else if (depth === 0 && separators.includes(ch)) {
            parts.push(current);
            current = '';
            continue;
        }
        current += ch;
    }
    parts.push(current);
    return parts.map(p => p.trim()).filter(Boolean);
}

function compileCompound(text, selector) {
    const tests = [];
    let rest = text;
    while (rest.length) {
        const m = SIMPLE.exec(rest);
        if (!m) throw new Error(`browser_sandbox: unsupported selector ${JSON.stringify(selector)}`);
        rest = rest.slice(m[0].length);
        if (m[1]) {
            const tag = m[1].toUpperCase();
            if (tag !== '*') tests.push(el => el.tagName === tag);
        } else if (m[2]) {
            const id = m[2];
            tests.push(el => el.id === id);
        } else if (m[3]) {
            const cls = m[3];
            tests.push(el => el.classList.contains(cls));
        } else if (m[4]) {
            const attr = m[4];
            const op = m[5];
            const want = m[6] !== undefined ? m[6] : (m[7] !== undefined ? m[7] : m[8]);
            tests.push(el => {
                const value = el.getAttribute(attr);
                if (value === null) return false;
                if (!op) return true;
                if (op === '=') return value === want;
                if (op === '^=') return value.startsWith(want);
                if (op === '$=') return value.endsWith(want);
                return value.includes(want);
            });
        } else if (m[9] === 'checked') {
            tests.push(el => el.checked === true);
        } else if (m[9] === 'disabled') {
            tests.push(el => el.disabled === true);
        }
    }
    return el => tests.every(t => t(el));
}

function compile(selector) {
    if (/[>+~]/.test(selector.replace(/\[[^\]]*\]/g, ''))) {
        throw new Error(`browser_sandbox: unsupported combinator in ${JSON.stringify(selector)}`);
    }
    const groups = splitOutside(selector, [',']).map(group => {
        const chain = splitOutside(group, [' ', '\n', '\t']).map(part => compileCompound(part, selector));
        return el => {
            if (!chain[chain.length - 1](el)) return false;
            let index = chain.length - 2;
            let node = el.parentNode;
            while (index >= 0 && node) {
                if (chain[index](node)) index -= 1;
                node = node.parentNode;
            }
            return index < 0;
        };
    });
    if (!groups.length) throw new Error(`browser_sandbox: empty selector ${JSON.stringify(selector)}`);
    return el => el instanceof FakeElement && groups.some(g => g(el));
}

// ---------------------------------------------------------------- elements

class ClassList {
    constructor() { this.names = new Set(); }
    add(...names) { names.forEach(n => this.names.add(n)); }
    remove(...names) { names.forEach(n => this.names.delete(n)); }
    contains(name) { return this.names.has(name); }
    toggle(name, force) {
        const on = force === undefined ? !this.names.has(name) : !!force;
        if (on) this.names.add(name); else this.names.delete(name);
        return on;
    }
    toString() { return Array.from(this.names).join(' '); }
}

class FakeEvent {
    constructor(type, init) {
        this.type = type;
        this.bubbles = !!(init && init.bubbles);
        this.defaultPrevented = false;
        this.target = null;
        this.currentTarget = null;
        this.key = init && init.key;
    }
    preventDefault() { this.defaultPrevented = true; }
    stopPropagation() { this.stopped = true; }
}

class FakeElement {
    constructor(doc, tagName) {
        this.ownerDocument = doc;
        this.tagName = String(tagName).toUpperCase();
        this.childNodes = [];
        this.parentNode = null;
        this.dataset = {};
        this.style = {};
        this.attrs = {};
        this.listeners = {};
        this.classList = new ClassList();
        this.ownText = '';
        this.id = '';
        this.name = '';
        this.type = '';
        this.value = '';
        this.href = '';
        this.checked = false;
        this.disabled = false;
        this.scrollTop = 0;
        this.scrollHeight = 0;
    }

    get children() { return this.childNodes.slice(); }
    get firstChild() { return this.childNodes[0] || null; }
    get parentElement() { return this.parentNode instanceof FakeElement ? this.parentNode : null; }
    get className() { return this.classList.toString(); }
    set className(value) {
        this.classList.names = new Set(String(value).split(/\s+/).filter(Boolean));
    }

    get textContent() {
        return this.ownText + this.childNodes.map(c => c.textContent).join('');
    }
    set textContent(value) {
        this.replaceChildren();
        this.ownText = value === null || value === undefined ? '' : String(value);
    }
    // No markup parsing: what is assigned is kept as text.
    get innerHTML() { return this.textContent; }
    set innerHTML(value) { this.textContent = value; }
    get innerText() { return this.textContent; }
    set innerText(value) { this.textContent = value; }

    appendChild(child) {
        if (child.parentNode) child.parentNode.removeChild(child);
        child.parentNode = this;
        this.childNodes.push(child);
        return child;
    }
    append(...nodes) {
        nodes.forEach(node => {
            if (node instanceof FakeElement) this.appendChild(node);
            else this.appendChild(this.ownerDocument.createTextNode(node));
        });
    }
    insertBefore(child, ref) {
        if (!ref) return this.appendChild(child);
        if (child.parentNode) child.parentNode.removeChild(child);
        child.parentNode = this;
        this.childNodes.splice(this.childNodes.indexOf(ref), 0, child);
        return child;
    }
    removeChild(child) {
        // As in a browser, detaching (or moving) the focused element, or one
        // that contains it, drops focus back to the body.
        const doc = this.ownerDocument;
        if (doc.activeElement && child.contains(doc.activeElement)) doc.activeElement = doc.body;
        const index = this.childNodes.indexOf(child);
        if (index >= 0) this.childNodes.splice(index, 1);
        child.parentNode = null;
        return child;
    }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    replaceChildren(...nodes) {
        this.childNodes.forEach(c => { c.parentNode = null; });
        this.childNodes = [];
        this.ownText = '';
        nodes.forEach(node => this.appendChild(node));
    }
    contains(other) {
        for (let node = other; node; node = node.parentNode) if (node === this) return true;
        return false;
    }

    setAttribute(name, value) {
        const text = String(value);
        if (name === 'id') this.id = text;
        else if (name === 'class') this.className = text;
        else if (name.startsWith('data-')) this.dataset[camel(name.slice(5))] = text;
        else if (name === 'checked' || name === 'disabled') this[name] = true;
        else if (['name', 'type', 'value', 'href'].includes(name)) this[name] = text;
        else this.attrs[name] = text;
    }
    getAttribute(name) {
        if (name === 'id') return this.id || null;
        if (name === 'class') return this.className || null;
        if (name.startsWith('data-')) {
            const value = this.dataset[camel(name.slice(5))];
            return value === undefined ? null : String(value);
        }
        if (name === 'checked' || name === 'disabled') return this[name] ? '' : null;
        if (['name', 'type', 'value', 'href'].includes(name)) return this[name] ? String(this[name]) : null;
        return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
    }
    hasAttribute(name) { return this.getAttribute(name) !== null; }
    removeAttribute(name) {
        if (name.startsWith('data-')) delete this.dataset[camel(name.slice(5))];
        else if (name === 'checked' || name === 'disabled') this[name] = false;
        else delete this.attrs[name];
    }

    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    removeEventListener(type, fn) {
        this.listeners[type] = (this.listeners[type] || []).filter(f => f !== fn);
    }
    dispatchEvent(event) {
        if (!event.target) event.target = this;
        let node = this;
        while (node) {
            event.currentTarget = node;
            (node.listeners[event.type] || []).slice().forEach(fn => fn.call(node, event));
            if (!event.bubbles || event.stopped) break;
            node = node.parentNode;
        }
        return !event.defaultPrevented;
    }

    descendants() {
        const out = [];
        const walk = node => node.childNodes.forEach(child => { out.push(child); walk(child); });
        walk(this);
        return out;
    }
    querySelectorAll(selector) {
        const match = compile(selector);
        return this.descendants().filter(match);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    matches(selector) { return compile(selector)(this); }
    closest(selector) {
        const match = compile(selector);
        for (let node = this; node instanceof FakeElement; node = node.parentNode) {
            if (match(node)) return node;
        }
        return null;
    }

    focus() { this.ownerDocument.activeElement = this; }
    blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body; }
    click() { this.dispatchEvent(new FakeEvent('click', { bubbles: true })); }
    scrollIntoView() {}
    getBoundingClientRect() { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
    // Every control is valid: constraint validation is the browser's job.
    get validity() { return { valid: true }; }
    checkValidity() { return true; }
    reportValidity() { return true; }
}

class FakeDocument {
    constructor(options) {
        this.autoCreateIds = !!options.autoCreateIds;
        this.created = new Map();
        this.listeners = {};
        this.documentElement = new FakeElement(this, 'html');
        this.head = this.documentElement.appendChild(new FakeElement(this, 'head'));
        this.body = this.documentElement.appendChild(new FakeElement(this, 'body'));
        this.activeElement = this.body;
        this.readyState = 'complete';
    }
    createElement(tag) { return new FakeElement(this, tag); }
    createTextNode(text) {
        const node = new FakeElement(this, '#text');
        node.ownText = String(text);
        return node;
    }
    // With autoCreateIds an unknown id gets a detached element (the same one
    // every time), so page code that assumes its markup exists can run.
    getElementById(id) {
        const found = this.documentElement.descendants().find(el => el.id === id);
        if (found) return found;
        if (!this.autoCreateIds) return null;
        if (!this.created.has(id)) {
            const el = this.createElement('div');
            el.id = id;
            this.created.set(id, el);
        }
        return this.created.get(id);
    }
    querySelectorAll(selector) { return this.documentElement.querySelectorAll(selector); }
    querySelector(selector) { return this.documentElement.querySelector(selector); }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    removeEventListener() {}
    // Every element with an id, in the tree or auto-created: id -> text.
    texts() {
        const out = {};
        this.created.forEach((el, id) => { out[id] = el.textContent; });
        this.documentElement.descendants().forEach(el => { if (el.id) out[el.id] = el.textContent; });
        return out;
    }
}

// ---------------------------------------------------------------- sandbox

function createSandbox(options) {
    options = options || {};
    const document = new FakeDocument(options);
    const record = { alerts: [], confirms: [], logs: [], fetches: [], reloads: 0, navigations: [], errors: [] };
    let respond = () => ({ status: 200, body: {} });

    const location = {
        pathname: '/',
        search: '',
        origin: 'http://localhost',
        reload() { record.reloads += 1; },
        assign(url) { record.navigations.push(String(url)); },
        replace(url) { record.navigations.push(String(url)); },
    };
    Object.defineProperty(location, 'href', {
        get() { return 'http://localhost/'; },
        set(url) { record.navigations.push(String(url)); },
    });

    const storage = () => {
        const data = new Map();
        return {
            getItem: k => (data.has(k) ? data.get(k) : null),
            setItem: (k, v) => data.set(k, String(v)),
            removeItem: k => data.delete(k),
        };
    };

    const log = (...args) => record.logs.push(args.map(String).join(' '));
    const sandbox = {
        document,
        location,
        console: { log, info: log, warn: log, error: log, debug: log },
        navigator: { userAgent: 'node' },
        localStorage: storage(),
        sessionStorage: storage(),
        Event: FakeEvent,
        CustomEvent: FakeEvent,
        HTMLElement: FakeElement,
        URL,
        URLSearchParams,
        FormData: class {
            constructor() { this.items = []; }
            entries() { return this.items[Symbol.iterator](); }
            get() { return null; }
        },
        alert: message => record.alerts.push(String(message)),
        confirm: message => {
            record.confirms.push(String(message));
            return options.confirm !== false;
        },
        // Timers run soon, in order, without the delay: a page waiting on one
        // (the range update pauses between weeks) still finishes. What a
        // callback throws is recorded, as a browser would log it.
        setTimeout: (fn, ms, ...args) => {
            setImmediate(() => {
                if (typeof fn !== 'function') return;
                try {
                    fn(...args);
                } catch (error) {
                    record.errors.push(String(error && error.message ? error.message : error));
                }
            });
            return 1;
        },
        clearTimeout: () => {},
        setInterval: () => 1,
        clearInterval: () => {},
        requestAnimationFrame: fn => { setImmediate(fn); return 1; },
        getComputedStyle: el => el.style,
        matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
        fetch: (url, init) => {
            const call = {
                url: String(url),
                method: (init && init.method) || 'GET',
                body: init && init.body !== undefined ? String(init.body) : null,
            };
            record.fetches.push(call);
            let reply;
            try {
                reply = respond(call);
            } catch (error) {
                return Promise.reject(error);
            }
            const status = reply.status === undefined ? 200 : reply.status;
            return Promise.resolve({
                ok: status >= 200 && status < 300,
                status,
                json: () => (reply.body === undefined
                    ? Promise.reject(new SyntaxError('Unexpected end of JSON input'))
                    : Promise.resolve(JSON.parse(JSON.stringify(reply.body)))),
            });
        },
    };
    const windowListeners = {};
    sandbox.addEventListener = (type, fn) => {
        (windowListeners[type] = windowListeners[type] || []).push(fn);
    };
    sandbox.removeEventListener = () => {};
    sandbox.window = sandbox;
    sandbox.self = sandbox;
    const context = vm.createContext(sandbox);

    return {
        window: sandbox,
        document,
        record,
        Event: FakeEvent,
        setResponder(fn) { respond = fn; },
        // Build rendered markup into the page: tree is {tag, attrs, children}
        // or {text} (tests/js_sandbox.py dom_tree() makes one from HTML).
        mount(tree, parent) {
            const build = node => {
                if (node.text !== undefined) return document.createTextNode(node.text);
                const el = document.createElement(node.tag);
                Object.keys(node.attrs || {}).forEach(name => el.setAttribute(name, node.attrs[name]));
                (node.children || []).forEach(child => el.appendChild(build(child)));
                return el;
            };
            return (parent || document.body).appendChild(build(tree));
        },
        load(path) {
            vm.runInContext(fs.readFileSync(path, 'utf8'), context, { filename: path });
        },
        loadCode(code, filename) {
            vm.runInContext(code, context, { filename: filename || 'inline.js' });
        },
        // Evaluate an expression in the page's global scope (top-level let
        // and const bindings, which are not properties of window).
        run(expression) {
            return vm.runInContext(expression, context);
        },
        async flush(rounds) {
            for (let i = 0; i < (rounds || 200); i++) {
                await new Promise(resolve => setImmediate(resolve));
            }
        },
        reset() {
            record.alerts = [];
            record.confirms = [];
            record.logs = [];
            record.fetches = [];
            record.reloads = 0;
            record.navigations = [];
            record.errors = [];
        },
        texts() { return document.texts(); },
    };
}

module.exports = { createSandbox, FakeElement, FakeEvent };

if (require.main === module) {
    const payload = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    const driver = new Function('data', 'createSandbox',
        `return (async () => {\n${payload.driver}\n})();`);
    driver(payload.data, createSandbox)
        .then(result => {
            process.stdout.write(JSON.stringify(result === undefined ? null : result));
        })
        .catch(error => {
            process.stderr.write(String(error && error.stack ? error.stack : error));
            process.exit(1);
        });
}
