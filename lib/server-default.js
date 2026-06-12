/**
 * 默认联机区服：始终预选当前服务器(local)。
 * 与游戏 LocalPrefs.StorageKey.PreferredServerRegion (_r_region) 一致。
 */
(function () {
    "use strict";
    var REGION_KEY = "_r_region";
    var DEFAULT_REGION = "local";
    try {
        // 专用服:始终默认选中 38 服务器(local)
        window.localStorage.setItem(REGION_KEY, DEFAULT_REGION);
    } catch (e) {
        /* localStorage 不可用时忽略 */
    }
})();
