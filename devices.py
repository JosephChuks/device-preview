"""Device presets and the per-device identity a website sees.

All sizes are CSS pixels (what a site's media queries see), not physical pixels.
"""
import json
from dataclasses import dataclass

CHROME_DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1")
IPAD_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
           "(KHTML, like Gecko) Version/26.0 Safari/605.1.15")  # iPadOS Safari reports as a Mac
SAMSUNG_UA = ("Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) "
              "SamsungBrowser/28.0 Chrome/130.0.0.0 Mobile Safari/537.36")
PIXEL_UA = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/141.0.0.0 Mobile Safari/537.36")


def _brands(*pairs):
    return [{"brand": b, "version": v} for b, v in pairs]


CHROME_BRANDS = _brands(("Google Chrome", "141"), ("Chromium", "141"), ("Not?A_Brand", "8"))
SAMSUNG_BRANDS = _brands(("Samsung Internet", "28.0"), ("Chromium", "130"), ("Not?A_Brand", "24"))


@dataclass(frozen=True)
class Device:
    key: str
    name: str
    group: str                  # menu section: Desktop / Phones / Tablets
    style: str                  # frame look: bare | monitor | island | homebutton | punchhole | tablet
    width: int                  # portrait CSS width of the whole screen
    height: int
    dpr: float                  # devicePixelRatio reported to the page
    user_agent: str
    platform: str               # navigator.platform
    vendor: str                 # navigator.vendor
    touch_points: int           # navigator.maxTouchPoints (0 = mouse device)
    ua_data: dict | None        # navigator.userAgentData; None = absent, as in Safari
    bezel: tuple                # (top, right, bottom, left) in portrait
    body_radius: int
    screen_radius: int
    insets_portrait: tuple      # screen area outside the page: status bar, home indicator...
    insets_landscape: tuple
    status_bar: str = ""        # ios | android | "" (none drawn)
    rotatable: bool = True


def _desktop(key, w, h):
    return Device(
        key=key, name=f"Desktop {w}×{h}", group="Desktop", style="bare",
        width=w, height=h, dpr=1.0, user_agent=CHROME_DESKTOP_UA, platform="Win32",
        vendor="Google Inc.", touch_points=0,
        ua_data={"brands": CHROME_BRANDS, "mobile": False, "platform": "Windows"},
        bezel=(0, 0, 0, 0), body_radius=0, screen_radius=0,
        insets_portrait=(0, 0, 0, 0), insets_landscape=(0, 0, 0, 0), rotatable=False,
    )


DEVICE_LIST = [
    _desktop("desktop-1440", 1440, 900),
    _desktop("desktop-1920", 1920, 1080),
    _desktop("desktop-1366", 1366, 768),
    Device(
        key="iphone-16-pro", name="iPhone 16 Pro", group="Phones", style="island",
        width=402, height=874, dpr=3.0, user_agent=IPHONE_UA, platform="iPhone",
        vendor="Apple Computer, Inc.", touch_points=5, ua_data=None,
        bezel=(12, 12, 12, 12), body_radius=62, screen_radius=52,
        insets_portrait=(54, 0, 34, 0), insets_landscape=(0, 59, 21, 59), status_bar="ios",
    ),
    Device(
        key="iphone-se", name="iPhone SE", group="Phones", style="homebutton",
        width=375, height=667, dpr=2.0, user_agent=IPHONE_UA, platform="iPhone",
        vendor="Apple Computer, Inc.", touch_points=5, ua_data=None,
        bezel=(84, 16, 84, 16), body_radius=52, screen_radius=2,
        insets_portrait=(20, 0, 0, 0), insets_landscape=(0, 0, 0, 0), status_bar="ios",
    ),
    Device(
        key="galaxy-s24", name="Samsung Galaxy S24", group="Phones", style="punchhole",
        width=360, height=780, dpr=3.0, user_agent=SAMSUNG_UA, platform="Linux armv81",
        vendor="Google Inc.", touch_points=5,
        ua_data={"brands": SAMSUNG_BRANDS, "mobile": True, "platform": "Android", "model": "SM-S921B"},
        bezel=(9, 9, 9, 9), body_radius=42, screen_radius=34,
        insets_portrait=(30, 0, 24, 0), insets_landscape=(0, 0, 0, 34), status_bar="android",
    ),
    Device(
        key="pixel-8", name="Google Pixel 8", group="Phones", style="punchhole",
        width=412, height=915, dpr=2.625, user_agent=PIXEL_UA, platform="Linux armv81",
        vendor="Google Inc.", touch_points=5,
        ua_data={"brands": CHROME_BRANDS, "mobile": True, "platform": "Android", "model": "Pixel 8"},
        bezel=(11, 11, 11, 11), body_radius=48, screen_radius=40,
        insets_portrait=(32, 0, 24, 0), insets_landscape=(0, 0, 0, 36), status_bar="android",
    ),
    Device(
        key="ipad", name="iPad (10th gen)", group="Tablets", style="tablet",
        width=820, height=1180, dpr=2.0, user_agent=IPAD_UA, platform="MacIntel",
        vendor="Apple Computer, Inc.", touch_points=5, ua_data=None,
        bezel=(26, 26, 26, 26), body_radius=40, screen_radius=18,
        insets_portrait=(24, 0, 20, 0), insets_landscape=(24, 0, 20, 0), status_bar="ios",
    ),
]

DEVICES = {d.key: d for d in DEVICE_LIST}
DEFAULT_LAYOUT = [{"key": "desktop-1440", "landscape": False},
                  {"key": "iphone-16-pro", "landscape": False}]


def spoof_script(device: Device, landscape: bool) -> str:
    """JavaScript run before any page script, so client-side checks see the device."""
    sw, sh = (device.height, device.width) if landscape else (device.width, device.height)
    data = {
        "ua": device.user_agent, "platform": device.platform, "vendor": device.vendor,
        "touchPoints": device.touch_points, "uaData": device.ua_data, "dpr": device.dpr,
        "screenW": sw, "screenH": sh, "landscape": landscape,
    }
    return _SPOOF_JS.replace("__DEVICE__", json.dumps(data))


_SPOOF_JS = r"""
(() => {
  const D = __DEVICE__;
  const def = (obj, prop, value) => {
    try { Object.defineProperty(obj, prop, { get: () => value, configurable: true }); } catch (e) {}
  };
  const nav = Navigator.prototype;
  def(nav, 'userAgent', D.ua);
  def(nav, 'appVersion', D.ua.replace(/^Mozilla\//, ''));
  def(nav, 'platform', D.platform);
  def(nav, 'vendor', D.vendor);
  def(nav, 'maxTouchPoints', D.touchPoints);

  if (D.uaData) {
    const ua = D.uaData;
    const data = {
      brands: ua.brands, mobile: ua.mobile, platform: ua.platform,
      getHighEntropyValues: () => Promise.resolve({
        brands: ua.brands, mobile: ua.mobile, platform: ua.platform,
        model: ua.model || '', platformVersion: '', architecture: ua.mobile ? 'arm' : 'x86',
        fullVersionList: ua.brands,
      }),
      toJSON: () => ({ brands: ua.brands, mobile: ua.mobile, platform: ua.platform }),
    };
    def(nav, 'userAgentData', data);
  } else {
    try { delete Navigator.prototype.userAgentData; } catch (e) {}
  }

  def(window, 'devicePixelRatio', D.dpr);
  const scr = Screen.prototype;
  def(scr, 'width', D.screenW);  def(scr, 'height', D.screenH);
  def(scr, 'availWidth', D.screenW);  def(scr, 'availHeight', D.screenH);

  if (D.touchPoints > 0) {
    if (!('ontouchstart' in window)) window.ontouchstart = null;
    const touchAnswers = {
      '(pointer: coarse)': true, '(pointer:coarse)': true,
      '(any-pointer: coarse)': true, '(any-pointer:coarse)': true,
      '(hover: none)': true, '(hover:none)': true,
      '(pointer: fine)': false, '(pointer:fine)': false,
      '(hover: hover)': false, '(hover:hover)': false,
    };
    const realMatchMedia = window.matchMedia.bind(window);
    window.matchMedia = (query) => {
      const k = String(query).trim().toLowerCase();
      if (k in touchAnswers) {
        return { matches: touchAnswers[k], media: query, onchange: null,
                 addListener() {}, removeListener() {},
                 addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } };
      }
      return realMatchMedia(query);
    };
  }
})();
"""
