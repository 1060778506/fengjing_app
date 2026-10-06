import { Calendar } from "fullcalendar/all";
import classicThemePlugin from "fullcalendar/themes/classic";
import zhCnLocale from "fullcalendar/locales/zh-cn";

import "fullcalendar/skeleton.css";
import "fullcalendar/themes/classic/theme.css";
import "fullcalendar/themes/classic/palette.css";

// Built separately for ES2020 and loaded only by the Fengjing calendar page.
window.FengjingFullCalendar = Object.freeze({ Calendar, classicThemePlugin, zhCnLocale });
