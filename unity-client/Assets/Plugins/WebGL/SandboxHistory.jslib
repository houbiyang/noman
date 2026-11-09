mergeInto(LibraryManager.library, {
  $QiongtuHistoryPrefix: function () {
    var pathname = window.location.pathname;
    return 'qiongtu:history:v1:' + pathname.substring(0, pathname.lastIndexOf('/') + 1) + ':';
  },
  QiongtuHistoryWrite__deps: ['$QiongtuHistoryPrefix', '$QiongtuReturnString'],
  QiongtuHistoryWrite: function (id, content) {
    var error = '';
    try {
      var identity = UTF8ToString(id);
      if (!/^[0-9a-f]{32}$/.test(identity)) throw new Error('历史编号无效');
      var key = QiongtuHistoryPrefix() + identity;
      if (localStorage.getItem(key) !== null) throw new Error('历史编号已存在，不覆盖原记录');
      localStorage.setItem(key, UTF8ToString(content));
    } catch (e) { error = e && e.message ? e.message : '浏览器拒绝存储访问'; }
    return QiongtuReturnString(error);
  },
  QiongtuHistoryRead__deps: ['$QiongtuHistoryPrefix', '$QiongtuReturnString'],
  QiongtuHistoryRead: function () {
    var result = { records: [], warning: '' };
    try {
      var prefix = QiongtuHistoryPrefix();
      for (var i = 0; i < localStorage.length; i++) {
        var key = localStorage.key(i);
        if (key && key.indexOf(prefix) === 0) result.records.push({ id: key.substring(prefix.length), content: localStorage.getItem(key) });
      }
    } catch (e) { result.warning = '浏览器历史读取失败：' + (e && e.message ? e.message : '存储不可用'); }
    return QiongtuReturnString(JSON.stringify(result));
  }
});
