mergeInto(LibraryManager.library, {
  // 同源的不同部署目录分别保存，查询参数和入口文件名不改变存档目录。
  $QiongtuStoragePrefix: function () {
    var pathname = window.location.pathname;
    return "qiongtu:sandbox:v1:" + pathname.substring(0, pathname.lastIndexOf("/") + 1) + ":";
  },

  // 返回字符串由 Unity 的互操作层释放。
  $QiongtuReturnString: function (value) {
    var pointer = _malloc(lengthBytesUTF8(value) + 1);
    stringToUTF8(value, pointer, lengthBytesUTF8(value) + 1);
    return pointer;
  },

  QiongtuStorageWrite__deps: ["$QiongtuStoragePrefix", "$QiongtuReturnString"],
  QiongtuStorageWrite: function (idPointer, contentPointer) {
    try {
      var id = UTF8ToString(idPointer);
      if (!/^[0-9a-f]{32}$/.test(id)) return QiongtuReturnString("存档编号无效");
      var key = QiongtuStoragePrefix() + id;
      if (window.localStorage.getItem(key) !== null) return QiongtuReturnString("存档编号已存在，未覆盖原记录");
      window.localStorage.setItem(key, UTF8ToString(contentPointer));
      return QiongtuReturnString("");
    } catch (error) {
      return QiongtuReturnString("站点存储不可用或空间不足，请检查浏览器的站点数据设置");
    }
  },

  QiongtuStorageRead__deps: ["$QiongtuStoragePrefix", "$QiongtuReturnString"],
  QiongtuStorageRead: function () {
    var result = { records: [], warning: "" };
    try {
      var prefix = QiongtuStoragePrefix();
      var storage = window.localStorage;
      for (var index = 0; index < storage.length; index++) {
        var key = storage.key(index);
        if (key && key.indexOf(prefix) === 0) {
          result.records.push({ id: key.substring(prefix.length), content: storage.getItem(key) });
        }
      }
    } catch (error) {
      result.warning = "无法读取浏览器存档，请检查站点存储是否被禁用";
    }
    return QiongtuReturnString(JSON.stringify(result));
  },

  QiongtuExit: function () {
    // 离开当前调用栈后再释放引擎，避免退出时仍有托管调用正在执行。
    window.setTimeout(function () { window.dispatchEvent(new Event("qiongtu-exit")); }, 0);
  }
});
