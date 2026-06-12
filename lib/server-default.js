/**
 * 默认联机区服：首次访问时预选 38 服务器(local)。
 * 与游戏 LocalPrefs.StorageKey.PreferredServerRegion (_r_region) 一致。
 */
(function () {
    "use strict";
    var REGION_KEY = "_r_region";
    var DEFAULT_REGION = "local";
    try {
        if (!window.localStorage.getItem(REGION_KEY)) {
            window.localStorage.setItem(REGION_KEY, DEFAULT_REGION);
        }
    } catch (e) {
        /* localStorage 不可用时忽略 */
    }
})();
