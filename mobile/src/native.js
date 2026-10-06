// Нативные плагины Capacitor для app.js: сборщик кладёт их в window.NativePlugins.
// app.js общий с веб-версией и сам проверяет, что из этого доступно.
import { SystemBars } from "@capacitor/core";
import { App } from "@capacitor/app";
import { BackgroundRunner } from "@capacitor/background-runner";
import { Browser } from "@capacitor/browser";
import { Clipboard } from "@capacitor/clipboard";
import { Dialog } from "@capacitor/dialog";
import { Filesystem } from "@capacitor/filesystem";
import { Haptics } from "@capacitor/haptics";
import { LocalNotifications } from "@capacitor/local-notifications";
import { Preferences } from "@capacitor/preferences";
import { Share } from "@capacitor/share";
import { SplashScreen } from "@capacitor/splash-screen";

window.NativePlugins = {
  App, BackgroundRunner, Browser, Clipboard, Dialog, Filesystem, Haptics, LocalNotifications, Preferences, Share,
  SplashScreen, SystemBars,
};
