"""Static file serving with no-store headers for mutable web assets."""

from fastapi.staticfiles import StaticFiles


class NoStoreStaticFiles(StaticFiles):
    def is_not_modified(self, response_headers, request_headers) -> bool:
        return False

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        asset = path.replace("\\", "/")
        is_vendor = asset == "vendor" or asset.startswith("vendor/")
        name = asset.rsplit("/", 1)[-1]
        if (not is_vendor) and (name.endswith(".js") or name == "app.css"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            if "etag" in response.headers:
                del response.headers["etag"]
        return response
