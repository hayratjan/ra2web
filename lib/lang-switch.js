/**
 * ra2web 前端多语言支持(无需改动打包后的游戏代码)。
 *
 * 原理:
 * 游戏在启动时通过 fetch 加载 config.ini(读取 defaultLanguage)
 * 与 res/locale/<语言>.json(界面翻译)。本脚本在游戏代码执行前
 * 拦截 fetch:
 *   1. 重写 config.ini 响应中的 defaultLanguage 为用户所选语言
 *      (同时影响 WOL 登录时上报的地区代码);
 *   2. 把所有 locale/<xx>.json 请求重定向到所选语言文件
 *      (覆盖游戏资源 CSF 语言对界面语言的强制覆盖)。
 *
 * 用户选择保存在 localStorage,页面右上角提供语言切换器。
 */
(function () {
    "use strict";

    // 站点可用语言(需与 res/locale/ 下的文件一一对应)
    var LANGUAGES = [
        { code: "zh-CN", label: "简体中文" },
        { code: "zh-TW", label: "繁體中文" },
        { code: "en-US", label: "English" },
    ];
    // 默认语言:简体中文
    var DEFAULT_LOCALE = "zh-CN";
    var STORAGE_KEY = "ra2web_locale";

    // 切换器标题的本地化文案
    var UI_TEXT = {
        "zh-CN": { title: "语言" },
        "zh-TW": { title: "語言" },
        "en-US": { title: "Language" },
    };

    function isSupported(code) {
        return LANGUAGES.some(function (lang) {
            return lang.code === code;
        });
    }

    /** 解析当前应使用的语言:本地存储 > 浏览器语言 > 默认值 */
    function resolveLocale() {
        try {
            var saved = window.localStorage.getItem(STORAGE_KEY);
            if (saved && isSupported(saved)) {
                return saved;
            }
        } catch (e) {
            /* localStorage 不可用时忽略 */
        }
        var nav = (navigator.language || "").toLowerCase();
        for (var i = 0; i < LANGUAGES.length; i++) {
            if (nav === LANGUAGES[i].code.toLowerCase()) {
                return LANGUAGES[i].code;
            }
        }
        // 模糊匹配语言主标签(如 zh-Hans -> zh-CN、en-GB -> en-US)
        var primary = nav.split("-")[0];
        if (primary === "zh") {
            return nav.indexOf("tw") >= 0 || nav.indexOf("hant") >= 0 || nav.indexOf("hk") >= 0
                ? "zh-TW"
                : "zh-CN";
        }
        for (var j = 0; j < LANGUAGES.length; j++) {
            if (LANGUAGES[j].code.toLowerCase().split("-")[0] === primary) {
                return LANGUAGES[j].code;
            }
        }
        return DEFAULT_LOCALE;
    }

    /** 当前站点根地址,用于把资源请求收敛到本机 Nginx 反代 */
    function currentSiteBase() {
        var host = window.location.hostname || "127.0.0.1";
        var port = window.location.port;
        var proto = window.location.protocol === "https:" ? "https" : "http";
        return port ? proto + "://" + host + ":" + port : proto + "://" + host;
    }

    /** 重写 config.ini:语言 + 资源地址改走当前服务器反代 */
    function rewriteConfigIni(text, locale) {
        var base = currentSiteBase();
        if (/^\s*defaultLanguage\s*=/m.test(text)) {
            text = text.replace(
                /^(\s*defaultLanguage\s*=\s*).*$/m,
                "$1" + locale
            );
        } else if (/^\s*\[General\]\s*$/m.test(text)) {
            text = text.replace(
                /^(\s*\[General\]\s*)$/m,
                "$1\ndefaultLanguage=" + locale
            );
        } else {
            text += "\ndefaultLanguage=" + locale + "\n";
        }
        text = text.replace(
            /^(\s*gameresBaseUrl\s*=\s*).*$/m,
            "$1" + base + "/gameres/"
        );
        text = text.replace(
            /^(\s*mapsBaseUrl\s*=\s*).*$/m,
            "$1" + base + "/maps/"
        );
        text = text.replace(
            /^(\s*modsBaseUrl\s*=\s*).*$/m,
            "$1" + base + "/mod/"
        );
        return text;
    }

    /** 将外部 CDN 请求改写为本站反代路径;屏蔽监控上报 */
    function rewriteExternalUrl(url) {
        if (!url) {
            return url;
        }
        // 屏蔽监控探针(含协议相对地址)
        if (/monitor-agent\.ra2web\.cn/i.test(url)) {
            return "";
        }
        // 旧版样式 CDN -> 本站
        if (/^https?:\/\/ra2web\.github\.io\//i.test(url)) {
            return url.replace(/^https?:\/\/ra2web\.github\.io\//i, "/");
        }
        // detect-gpu 基准数据 -> 本站 Nginx 反代
        if (/^https?:\/\/unpkg\.com\/detect-gpu@5\.0\.42\/dist\//i.test(url)) {
            return url.replace(
                /^https?:\/\/unpkg\.com\/detect-gpu@5\.0\.42\/dist\//i,
                "/vendor/detect-gpu/"
            );
        }
        // 游戏资源 CDN(协议相对或完整 URL) -> 本站 /gameres/
        if (/^\/\/wyhjres(?:2)?\.bun\.sh\.cn\//i.test(url)) {
            return url.replace(/^\/\/wyhjres(?:2)?\.bun\.sh\.cn\//i, "/gameres/");
        }
        if (/^https?:\/\/wyhjres(?:2)?\.bun\.sh\.cn\//i.test(url)) {
            return url.replace(
                /^https?:\/\/wyhjres(?:2)?\.bun\.sh\.cn\//i,
                "/gameres/"
            );
        }
        return url;
    }

    /** 强制覆盖联机大厅相关翻译(CSF 内建中文会显示「自定义房间」) */
    function patchLocalePayload(text, locale) {
        var data;
        try {
            data = JSON.parse(text);
        } catch (e) {
            return text;
        }
        if (locale === "zh-TW") {
            data["gui:custommatch"] = "聯機大廳";
            data["stt:wolwelcomecustommatch"] =
                "登入當前伺服器，進入聯機大廳建立或加入房間，支援多人對戰";
        } else if (locale === "zh-CN") {
            data["gui:custommatch"] = "联机大厅";
            data["stt:wolwelcomecustommatch"] =
                "登录当前服务器，进入联机大厅创建或加入房间，支持多人对战";
        } else {
            data["gui:custommatch"] = "Online Lobby";
            data["stt:wolwelcomecustommatch"] =
                "Join the online lobby on this server to create or join rooms";
        }
        return JSON.stringify(data);
    }

    /** 把 locale/<xx>.json 的请求地址重写为所选语言 */
    function rewriteLocaleUrl(url, locale) {
        return url.replace(
            /(^|\/)locale\/[A-Za-z0-9_-]+\.json/,
            "$1locale/" + locale + ".json"
        );
    }

    function isConfigIniUrl(url) {
        return /(^|\/)config\.ini(\?|$)/.test(url);
    }

    function isLocaleJsonUrl(url) {
        return /(^|\/)locale\/[A-Za-z0-9_-]+\.json(\?|$)/.test(url);
    }

    function isServersIniUrl(url) {
        return /(^|\/)servers\.ini(\?|$)/.test(url);
    }

    /**
     * 生成仅含「当前服务器」的 servers.ini。
     * 联机大厅/排位赛登录时默认连接用户正在访问的这台主机。
     */
    function buildCurrentServersIni() {
        var host = window.location.hostname || "127.0.0.1";
        var port = window.location.port;
        var isHttps = window.location.protocol === "https:";
        var wsProto = isHttps ? "wss" : "ws";
        var httpProto = isHttps ? "https" : "http";
        var hostPort = port ? host + ":" + port : host;
        var label = "当前服务器 (" + hostPort + ")";

        return (
            "[local]\n" +
            'label="' + label + '"\n' +
            "available=yes\n" +
            "gameVersion=0.65.1\n" +
            'wolUrl="' + wsProto + "://" + hostPort + '/wol"\n' +
            'wladderUrl="' + httpProto + "://" + hostPort + '/ladder"\n' +
            'wgameresUrl="' + httpProto + "://" + hostPort + '/wgameres"\n' +
            'gservUrl="' + wsProto + "://" + hostPort + '/gserv"\n' +
            "wolKeepAliveInGame=yes\n"
        );
    }

    /** 统一处理请求 URL(外网改写 / 翻译文件 / 配置) */
    function resolveRequestUrl(url, locale) {
        var external = rewriteExternalUrl(url);
        if (external === "") {
            return { blocked: true };
        }
        if (external !== url) {
            return { url: external };
        }
        if (isLocaleJsonUrl(url)) {
            return { url: rewriteLocaleUrl(url, locale), localePatch: true };
        }
        if (isServersIniUrl(url)) {
            return { serversIni: true };
        }
        if (isConfigIniUrl(url)) {
            return { configIni: true, url: url };
        }
        return { url: url };
    }

    /** 安装 fetch 拦截器 */
    function installFetchHook(locale) {
        var originalFetch = window.fetch.bind(window);
        window.fetch = function (input, init) {
            try {
                var isRequestObj = typeof input !== "string" && input && input.url;
                var url = typeof input === "string" ? input : (isRequestObj ? input.url : "");
                var resolved = resolveRequestUrl(url, locale);

                if (resolved.blocked) {
                    return Promise.resolve(
                        new Response("{}", {
                            status: 204,
                            statusText: "No Content",
                        })
                    );
                }
                if (resolved.serversIni) {
                    return Promise.resolve(
                        new Response(buildCurrentServersIni(), {
                            status: 200,
                            statusText: "OK",
                            headers: { "Content-Type": "text/plain; charset=utf-8" },
                        })
                    );
                }
                if (resolved.configIni) {
                    return originalFetch(input, init).then(function (response) {
                        if (!response.ok) {
                            return response;
                        }
                        return response.text().then(function (text) {
                            return new Response(rewriteConfigIni(text, locale), {
                                status: response.status,
                                statusText: response.statusText,
                                headers: { "Content-Type": "text/plain; charset=utf-8" },
                            });
                        }).catch(function () {
                            return originalFetch(input, init);
                        });
                    });
                }
                if (resolved.url !== url) {
                    if (isRequestObj && typeof Request === "function") {
                        input = new Request(resolved.url, input);
                    } else {
                        input = resolved.url;
                    }
                }
                if (resolved.localePatch) {
                    return originalFetch(input, init).then(function (response) {
                        if (!response.ok) {
                            return response;
                        }
                        return response.text().then(function (text) {
                            return new Response(patchLocalePayload(text, locale), {
                                status: response.status,
                                statusText: response.statusText,
                                headers: { "Content-Type": "application/json; charset=utf-8" },
                            });
                        });
                    });
                }
            } catch (e) {
                // 拦截器自身异常不能影响游戏正常加载
            }
            return originalFetch(input, init);
        };
    }

    /** 安装 XMLHttpRequest 拦截器(部分旧代码不走 fetch) */
    function installXhrHook(locale) {
        if (typeof XMLHttpRequest === "undefined") {
            return;
        }
        var origOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function (method, url) {
            try {
                var args = Array.prototype.slice.call(arguments);
                var resolved = resolveRequestUrl(String(url || ""), locale);
                if (resolved.blocked) {
                    args[1] = "data:text/plain,";
                } else if (resolved.serversIni) {
                    this.__ra2webServersIni = true;
                    args[1] = "data:text/plain,";
                } else if (resolved.configIni) {
                    this.__ra2webConfigIni = true;
                } else if (resolved.url) {
                    args[1] = resolved.url;
                }
                if (resolved.localePatch) {
                    this.__ra2webLocalePatch = true;
                }
                return origOpen.apply(this, args);
            } catch (e) {
                return origOpen.apply(this, arguments);
            }
        };

        var origSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function () {
            var xhr = this;
            if (xhr.__ra2webServersIni) {
                xhr.addEventListener("readystatechange", function onReady() {
                    if (xhr.readyState === 4) {
                        Object.defineProperty(xhr, "responseText", {
                            value: buildCurrentServersIni(),
                        });
                        Object.defineProperty(xhr, "status", { value: 200 });
                    }
                });
            }
            if (xhr.__ra2webLocalePatch) {
                xhr.addEventListener("load", function () {
                    try {
                        Object.defineProperty(xhr, "responseText", {
                            value: patchLocalePayload(xhr.responseText, locale),
                        });
                    } catch (e) {
                        /* 忽略 */
                    }
                });
            }
            return origSend.apply(this, arguments);
        };
    }

    /** 创建右上角语言切换器 */
    function createSwitcher(locale) {
        var container = document.createElement("div");
        container.id = "ra2web-lang-switch";
        container.style.cssText =
            "position:fixed;top:8px;right:8px;z-index:99999;" +
            "font:12px/1.4 sans-serif;color:#cdcdcd;opacity:0.85;" +
            "background:rgba(0,0,0,0.55);border:1px solid #555;" +
            "border-radius:4px;padding:4px 6px;display:flex;" +
            "align-items:center;gap:4px;";

        var text = UI_TEXT[locale] || UI_TEXT[DEFAULT_LOCALE];
        var label = document.createElement("span");
        label.textContent = text.title;
        container.appendChild(label);

        var select = document.createElement("select");
        select.style.cssText =
            "background:#222;color:#cdcdcd;border:1px solid #555;" +
            "border-radius:3px;font-size:12px;padding:1px 2px;";
        LANGUAGES.forEach(function (lang) {
            var option = document.createElement("option");
            option.value = lang.code;
            option.textContent = lang.label;
            if (lang.code === locale) {
                option.selected = true;
            }
            select.appendChild(option);
        });
        select.addEventListener("change", function () {
            try {
                window.localStorage.setItem(STORAGE_KEY, select.value);
            } catch (e) {
                /* 忽略 */
            }
            // 重新加载页面以重新引导所有翻译资源
            window.location.reload();
        });
        container.appendChild(select);

        // 进入全屏(游戏中)时自动隐藏,避免遮挡画面
        document.addEventListener("fullscreenchange", function () {
            container.style.display = document.fullscreenElement ? "none" : "flex";
        });

        document.body.appendChild(container);
    }

    // 浏览器环境下执行引导(Node 单测环境跳过)
    if (typeof window !== "undefined") {
        var currentLocale = resolveLocale();

        // 对外暴露查询/切换接口
        window.ra2webLang = {
            get: function () {
                return currentLocale;
            },
            set: function (code) {
                if (!isSupported(code)) {
                    throw new Error("Unsupported locale: " + code);
                }
                window.localStorage.setItem(STORAGE_KEY, code);
                window.location.reload();
            },
            languages: LANGUAGES.slice(),
        };

        if (typeof window.fetch === "function") {
            installFetchHook(currentLocale);
        }
        installXhrHook(currentLocale);

        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", function () {
                createSwitcher(currentLocale);
            });
        } else {
            createSwitcher(currentLocale);
        }
    }

    // 供 Node 环境单元测试使用(浏览器中无副作用)
    if (typeof module !== "undefined" && module.exports) {
        module.exports = {
            rewriteConfigIni: rewriteConfigIni,
            rewriteLocaleUrl: rewriteLocaleUrl,
            rewriteExternalUrl: rewriteExternalUrl,
            patchLocalePayload: patchLocalePayload,
            resolveRequestUrl: resolveRequestUrl,
            currentSiteBase: currentSiteBase,
            buildCurrentServersIni: buildCurrentServersIni,
            isConfigIniUrl: isConfigIniUrl,
            isLocaleJsonUrl: isLocaleJsonUrl,
            isServersIniUrl: isServersIniUrl,
        };
    }
})();
