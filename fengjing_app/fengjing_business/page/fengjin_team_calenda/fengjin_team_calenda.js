const FENGJING_CALENDAR_METHOD =
	"fengjing_app.fengjing_business.page.fengjin_team_calenda.fengjin_team_calenda";

frappe.pages["fengjin_team_calenda"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "丰境日历",
		single_column: true,
	});
	wrapper.fengjing_team_calendar = new FengjingTeamCalendarPage(wrapper, page);
};

frappe.pages["fengjin_team_calenda"].on_page_show = function (wrapper) {
	wrapper.fengjing_team_calendar?.show();
};

frappe.pages["fengjin_team_calenda"].on_page_hide = function (wrapper) {
	wrapper.fengjing_team_calendar?.hide();
};

class FengjingTeamCalendarPage {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.calendar = null;
		this.loadingPromise = null;
		this.renderShell();
		this.addActions();
	}

	renderShell() {
		$(this.wrapper).addClass("fjtc-page");
		this.$main = $(this.page.main);
		this.$main.html(`
			<div class="fjtc-shell">
				<div class="fjtc-loading">
					<span class="fjtc-spinner"></span>
					<span>正在加载丰境日历…</span>
				</div>
				<div class="fjtc-calendar" aria-label="丰境日历"></div>
			</div>
		`);
		this.container = this.$main.find(".fjtc-calendar")[0];
	}

	addActions() {
		this.page.set_primary_action("新建日程", () => this.openEventDialog(), "add");
		this.page.add_inner_button("刷新", () => this.calendar?.refetchEvents());
		this.page.add_inner_button("日程列表", () => frappe.set_route("List", "Event"));
	}

	async show() {
		try {
			await this.ensureCalendar();
			// FullCalendar 7 no longer exposes updateSize(). Re-render after the
			// Frappe page becomes visible so it can measure the real container.
			this.calendar.render();
			this.calendar.refetchEvents();
		} catch (error) {
			this.showError("日历加载失败", error);
		}
	}

	hide() {}

	ensureCalendar() {
		if (this.calendar) return Promise.resolve(this.calendar);
		if (this.loadingPromise) return this.loadingPromise;
		this.loadingPromise = new Promise((resolve, reject) => {
			frappe.require([
				"/assets/fengjing_app/calendar/fullcalendar/fengjing_calendar.css",
				"/assets/fengjing_app/calendar/fullcalendar/fengjing_calendar.js",
			], () => {
				try {
					const library = window.FengjingFullCalendar;
					if (!library?.Calendar) throw new Error("未找到 FullCalendar 构建资源。");
					this.calendar = this.createCalendar(library);
					this.$main.find(".fjtc-loading").hide();
					this.$main.find(".fjtc-calendar").show();
					this.calendar.render();
					resolve(this.calendar);
				} catch (error) {
					reject(error);
				}
			});
		}).finally(() => {
			this.loadingPromise = null;
		});
		return this.loadingPromise;
	}

	createCalendar(library) {
		return new library.Calendar(this.container, {
			plugins: [library.classicThemePlugin],
			themeSystem: "classic",
			locale: library.zhCnLocale,
			initialView: "dayGridMonth",
			height: "auto",
			contentHeight: "auto",
			firstDay: 1,
			nowIndicator: true,
			navLinks: true,
			selectable: true,
			selectMirror: true,
			editable: true,
			eventStartEditable: true,
			eventDurationEditable: true,
			dayMaxEvents: true,
			displayEventEnd: true,
			buttonText: {
				today: "今天",
				month: "月",
				week: "周",
				day: "日",
				list: "列表",
			},
			headerToolbar: {
				left: "prev,next today",
				center: "title",
				right: "dayGridMonth,timeGridWeek,timeGridDay,listMonth",
			},
			events: (info, success, failure) => this.loadEvents(info, success, failure),
			select: (info) => this.openEventDialog(null, this.selectionDefaults(info)),
			eventClick: (info) => this.openEventDialog(info.event.extendedProps.event_name),
			eventDrop: (info) => this.persistCalendarMove(info),
			eventResize: (info) => this.persistCalendarMove(info),
			eventDidMount: (info) => {
				const props = info.event.extendedProps;
				info.el.title = props.repeat_this_event
					? `${info.event.title} · 重复日程需在完整表单中修改`
					: info.event.title;
			},
		});
	}

	async loadEvents(info, success, failure) {
		try {
			const endInclusive = new Date(info.end.getTime() - 1);
			const response = await frappe.call({
				method: `${FENGJING_CALENDAR_METHOD}.get_calendar_events`,
				args: {
					start: this.dateOnly(info.start),
					end: this.dateOnly(endInclusive),
				},
			});
			success(response.message || []);
		} catch (error) {
			failure(error);
			this.showError("日程读取失败", error);
		}
	}

	selectionDefaults(info) {
		let end = info.end;
		if (info.allDay) end = new Date(info.end.getTime() - 1000);
		return {
			starts_on: this.dateTime(info.start),
			ends_on: this.dateTime(end),
			all_day: info.allDay ? 1 : 0,
		};
	}

	async openEventDialog(name = null, defaults = {}) {
		let event = {
			subject: "",
			starts_on: defaults.starts_on || this.dateTime(new Date()),
			ends_on: defaults.ends_on || this.dateTime(new Date(Date.now() + 60 * 60 * 1000)),
			all_day: defaults.all_day || 0,
			event_type: "Private",
			event_category: "Event",
			color: "",
			description: "",
			status: "Open",
			send_reminder: 1,
			can_write: true,
			can_delete: false,
		};

		if (name) {
			try {
				const response = await frappe.call({
					method: `${FENGJING_CALENDAR_METHOD}.get_event`,
					args: { name },
				});
				event = { ...event, ...(response.message || {}) };
			} catch (error) {
				this.showError("日程读取失败", error);
				return;
			}
		}

		const readOnly = Boolean(name && !event.can_write);
		const dialog = new frappe.ui.Dialog({
			title: name ? "编辑日程" : "新建日程",
			size: "large",
			fields: [
				{ fieldname: "subject", fieldtype: "Small Text", label: "日程标题", reqd: 1, read_only: readOnly },
				{ fieldname: "starts_on", fieldtype: "Datetime", label: "开始时间", reqd: 1, read_only: readOnly },
				{ fieldname: "ends_on", fieldtype: "Datetime", label: "结束时间", read_only: readOnly },
				{ fieldname: "time_column", fieldtype: "Column Break" },
				{ fieldname: "all_day", fieldtype: "Check", label: "全天日程", read_only: readOnly },
				{ fieldname: "send_reminder", fieldtype: "Check", label: "当天早上发送邮件提醒", read_only: readOnly },
				{ fieldname: "event_type", fieldtype: "Select", label: "可见范围", options: ["Private", "Public"], reqd: 1, read_only: readOnly },
				{ fieldname: "event_category", fieldtype: "Select", label: "日程类型", options: ["Event", "Meeting", "Call", "Sent/Received Email", "Other"], read_only: readOnly },
				{ fieldname: "status", fieldtype: "Select", label: "状态", options: ["Open", "Completed", "Closed", "Cancelled"], read_only: readOnly },
				{ fieldname: "color", fieldtype: "Color", label: "显示颜色", read_only: readOnly },
				{ fieldname: "description_section", fieldtype: "Section Break" },
				{ fieldname: "description", fieldtype: "Text Editor", label: "日程说明", read_only: readOnly },
			],
			primary_action_label: readOnly ? "关闭" : "保存",
			primary_action: async (values) => {
				if (readOnly) {
					dialog.hide();
					return;
				}
				try {
					await frappe.call({
						method: `${FENGJING_CALENDAR_METHOD}.${name ? "update_event" : "create_event"}`,
						args: name ? { name, values } : { values },
						freeze: true,
						freeze_message: "正在保存日程…",
					});
					dialog.hide();
					this.calendar?.unselect();
					this.calendar?.refetchEvents();
					frappe.show_alert({ message: "日程已保存", indicator: "green" });
				} catch (error) {
					this.showError("日程保存失败", error);
				}
			},
			secondary_action_label: name ? "打开完整日程" : null,
			secondary_action: name
				? () => {
					dialog.hide();
					frappe.set_route("Form", "Event", name);
				}
				: null,
		});
		dialog.show();
		await dialog.set_values(event);

		if (name && event.can_delete) {
			const $delete = $('<button type="button" class="btn btn-danger btn-sm fjtc-delete-event">删除日程</button>');
			dialog.$wrapper.find(".modal-footer").prepend($delete);
			$delete.on("click", () => {
				frappe.confirm(`确定删除日程“${frappe.utils.escape_html(event.subject)}”吗？`, async () => {
					try {
						await frappe.call({
							method: `${FENGJING_CALENDAR_METHOD}.delete_event`,
							args: { name },
							freeze: true,
							freeze_message: "正在删除日程…",
						});
						dialog.hide();
						this.calendar?.refetchEvents();
						frappe.show_alert({ message: "日程已删除", indicator: "green" });
					} catch (error) {
						this.showError("日程删除失败", error);
					}
				});
			});
		}

		if (event.repeat_this_event) {
			dialog.set_message("这是重复日程。修改重复规则请打开完整日程表单。");
		}
	}

	async persistCalendarMove(info) {
		const event = info.event;
		try {
			await frappe.call({
				method: `${FENGJING_CALENDAR_METHOD}.move_event`,
				args: {
					name: event.extendedProps.event_name,
					...this.calendarEventTimes(event),
				},
				freeze: true,
				freeze_message: "正在更新日程时间…",
			});
			frappe.show_alert({ message: "日程时间已更新", indicator: "green" });
		} catch (error) {
			info.revert();
			this.showError("日程时间更新失败", error);
		}
	}

	calendarEventTimes(event) {
		let end = event.end;
		if (event.allDay) {
			end = new Date((end || new Date(event.start.getTime() + 24 * 60 * 60 * 1000)).getTime() - 1000);
		}
		return {
			starts_on: this.dateTime(event.start),
			ends_on: end ? this.dateTime(end) : null,
			all_day: event.allDay ? 1 : 0,
		};
	}

	dateOnly(date) {
		return `${date.getFullYear()}-${this.pad(date.getMonth() + 1)}-${this.pad(date.getDate())}`;
	}

	dateTime(date) {
		return `${this.dateOnly(date)} ${this.pad(date.getHours())}:${this.pad(date.getMinutes())}:${this.pad(date.getSeconds())}`;
	}

	pad(value) {
		return String(value).padStart(2, "0");
	}

	showError(title, error) {
		console.error(error);
		frappe.msgprint({
			title,
			message: error?.message || error?._server_messages || "请稍后重试或查看错误日志。",
			indicator: "red",
		});
	}
}
