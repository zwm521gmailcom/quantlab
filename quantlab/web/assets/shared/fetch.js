(function (w) {
  w.apiFetch = async function (url, options) {
    const response = await fetch(url, options);
    const body = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      const error = new Error(body.message || body.error_code || response.statusText);
      error.status = response.status;
      error.body = body;
      throw error;
    }
    return body;
  };
})(window);
