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

    /** 重写 config.ini 文本中的 defaultLanguage 配置项 */
    function rewriteConfigIni(text, locale) {
        if (/^\s*defaultLanguage\s*=/m.test(text)) {
            return text.replace(
                /^(\s*defaultLanguage\s*=\s*).*$/m,
                "$1" + locale
            );
        }
        // 配置缺失时插入到 [General] 段(找不到段则追加到文末)
        if (/^\s*\[General\]\s*$/m.test(text)) {
            return text.replace(
                /^(\s*\[General\]\s*)$/m,
                "$1\ndefaultLanguage=" + locale
            );
        }
        return text + "\ndefaultLanguage=" + locale + "\n";
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

    /** 安装 fetch 拦截器 */
    function installFetchHook(locale) {
        var originalFetch = window.fetch.bind(window);
        window.fetch = function (input, init) {
            try {
                var isRequestObj = typeof input !== "string" && input && input.url;
                var url = typeof input === "string" ? input : (isRequestObj ? input.url : "");
                if (isLocaleJsonUrl(url)) {
                    var rewritten = rewriteLocaleUrl(url, locale);
                    // Request 对象输入时继承其请求参数(翻译文件均为 GET)
                    if (isRequestObj && typeof Request === "function") {
                        return originalFetch(new Request(rewritten, input));
                    }
                    return originalFetch(rewritten, init);
                }
                if (isServersIniUrl(url)) {
                    return Promise.resolve(
                        new Response(buildCurrentServersIni(), {
                            status: 200,
                            statusText: "OK",
                            headers: { "Content-Type": "text/plain; charset=utf-8" },
                        })
                    );
                }
                if (isConfigIniUrl(url)) {
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
                            // 改写失败时退回原始响应行为:重新请求原地址
                            return originalFetch(input, init);
                        });
                    });
                }
            } catch (e) {
                // 拦截器自身异常不能影响游戏正常加载
            }
            return originalFetch(input, init);
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
            buildCurrentServersIni: buildCurrentServersIni,
            isConfigIniUrl: isConfigIniUrl,
            isLocaleJsonUrl: isLocaleJsonUrl,
            isServersIniUrl: isServersIniUrl,
        };
    }
})();
