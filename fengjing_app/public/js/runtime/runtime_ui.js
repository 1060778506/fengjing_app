// 丰境日常运行所需的通用界面增强。

// 自动跳转到树状图科目表
frappe.router.on('change', () => {
    let route = frappe.get_route();

    if (!route) return;

    // 判断是不是 Account 的默认 List 视图
    if (route[0] === "List" && route[1] === "Account") {

        // 如果不是 Tree 视图才跳转（防止死循环）
        if (!(route[2] && route[2] === "Tree")) {

            console.log("--- ⚡ 自动跳转到 Account Tree 视图 ---");

            frappe.set_route("List", "Account", "Tree");
        }
    }
});

// 默认不勾选优化选项
(() => {
    const OPTIMIZE_SELECTOR =
        '#uploader-optimize-checkbox input[type="checkbox"]';

    function disable_default_optimization(root = document) {
        root.querySelectorAll(OPTIMIZE_SELECTOR).forEach((checkbox) => {
            // 每个上传项只自动处理一次，之后仍允许用户手动重新勾选
            if (checkbox.dataset.fengjingDefaultApplied === "1") {
                return;
            }

            checkbox.dataset.fengjingDefaultApplied = "1";

            // 必须触发点击事件，才能同时修改 Vue 内部的 file.optimize
            if (checkbox.checked) {
                checkbox.click();
            }
        });
    }

    function start_observer() {
        disable_default_optimization();

        const observer = new MutationObserver(() => {
            disable_default_optimization();
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }

    if (document.body) {
        start_observer();
    } else {
        document.addEventListener("DOMContentLoaded", start_observer, {
            once: true
        });
    }
})();

// ============================================================
// 物料图片增强
// 1. items子表的物料编号左侧显示缩略图
// 2. 鼠标经过缩略图显示完整大图
// 3. Item物料下拉菜单显示图片并美化
// 4. Item物料下拉菜单批量选择物料
// ============================================================
(() => {
    if (window.__fengjing_item_image_module_loaded) {
        return;
    }

    window.__fengjing_item_image_module_loaded = true;

    const 支持的单据 = [
        "Stock Entry",
        "Material Request",
        "Request for Quotation",
        "Supplier Quotation",
        "Purchase Order",
        "Purchase Receipt",
        "Purchase Invoice",
        "Opportunity",
        "Quotation",
        "Sales Order",
        "Delivery Note",
        "Sales Invoice",
        "POS Invoice",
        "BOM",
        "Subcontracting Order",
        "Subcontracting Receipt"
    ];

    // 缓存物料图片，避免重复查询
    const 物料图片缓存 = new Map();

    // --------------------------------------------------------
    // 样式
    // --------------------------------------------------------
    function 添加样式() {
        if (document.getElementById("fengjing-item-image-style")) {
            return;
        }

        const style = document.createElement("style");
        style.id = "fengjing-item-image-style";

        style.textContent = `
            /* items表格中的物料图片 */
            .fengjing-item-cell {
                display: flex;
                align-items: center;
                gap: 7px;
                min-height: 28px;
                max-width: 100%;
                overflow: hidden;
            }

            .fengjing-item-thumbnail {
                width: 26px;
                height: 26px;
                flex: 0 0 26px;
                display: flex;
                align-items: center;
                justify-content: center;
                box-sizing: border-box;
                padding: 2px;
                overflow: hidden;
                border: 1px solid #e2e8f0;
                border-radius: 5px;
                background: #ffffff;
                cursor: zoom-in;
                transition:
                    border-color 0.15s ease,
                    box-shadow 0.15s ease,
                    transform 0.15s ease;
            }

            .fengjing-item-thumbnail:hover {
                z-index: 2;
                border-color: #7c9cff;
                box-shadow: 0 2px 8px rgba(37, 99, 235, 0.20);
                transform: translateY(-1px);
            }

            .fengjing-item-thumbnail img {
                display: block;
                width: auto;
                height: auto;
                max-width: 100%;
                max-height: 100%;
                object-fit: contain;
                object-position: center center;
            }

            .fengjing-item-code {
                min-width: 0;
                overflow: hidden;
                white-space: nowrap;
                text-overflow: ellipsis;
            }

            /* 鼠标悬停大图 */
            #fengjing-item-image-preview {
                position: fixed;
                z-index: 1000000;
                display: none;
                align-items: center;
                justify-content: center;
                width: min(480px, calc(100vw - 32px));
                height: min(480px, calc(100vh - 32px));
                box-sizing: border-box;
                padding: 14px;
                overflow: hidden;
                pointer-events: none;
                border: 1px solid rgba(15, 23, 42, 0.12);
                border-radius: 12px;
                background:
                    linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
                box-shadow:
                    0 22px 55px rgba(15, 23, 42, 0.24),
                    0 6px 18px rgba(15, 23, 42, 0.12);
            }

            #fengjing-item-image-preview img {
                display: block;
                width: auto;
                height: auto;
                max-width: 100%;
                max-height: 100%;
                object-fit: contain;
                object-position: center center;
            }

            /* 点击表格缩略图后显示的沉浸式大图 */
            #fengjing-item-image-modal {
                position: fixed;
                inset: 0;
                z-index: 1100000;
                display: none;
                align-items: center;
                justify-content: center;
                box-sizing: border-box;
                padding: 32px;
                background: rgba(0, 0, 0, 0.78);
                backdrop-filter: blur(2px);
                cursor: zoom-out;
            }

            #fengjing-item-image-modal.is-open {
                display: flex;
            }

            #fengjing-item-image-modal-content {
                display: flex;
                align-items: center;
                justify-content: center;
                max-width: 94vw;
                max-height: 92vh;
                cursor: default;
            }

            #fengjing-item-image-modal-content img {
                display: block;
                width: auto;
                height: auto;
                max-width: 94vw;
                max-height: 92vh;
                object-fit: contain;
                object-position: center center;
                border-radius: 10px;
                background: #ffffff;
                box-shadow: 0 28px 80px rgba(0, 0, 0, 0.48);
            }

            /* 物料下拉菜单 */
            .fengjing-item-dropdown {
                width: min(500px, calc(100vw - 30px)) !important;
                max-width: min(500px, calc(100vw - 30px)) !important;
                padding: 6px !important;
                overflow-x: hidden !important;
                border: 1px solid #dbe3ef !important;
                border-radius: 10px !important;
                background: #ffffff !important;
                box-shadow:
                    0 16px 38px rgba(15, 23, 42, 0.16),
                    0 4px 12px rgba(15, 23, 42, 0.08) !important;
            }

            .fengjing-item-dropdown [role="option"] {
                box-sizing: border-box;
                margin: 2px 0 !important;
                padding: 0 !important;
                overflow: hidden;
                border: 1px solid transparent;
                border-radius: 8px;
                transition:
                    background-color 0.12s ease,
                    border-color 0.12s ease;
            }

            .fengjing-item-dropdown [role="option"]:hover,
            .fengjing-item-dropdown [role="option"][aria-selected="true"] {
                border-color: #c7d7fe;
                background: #eff6ff !important;
            }

            .fengjing-item-dropdown [role="option"] > p {
                margin: 0 !important;
                padding: 0 !important;
            }

            .fengjing-dropdown-item {
                display: flex;
                align-items: center;
                gap: 10px;
                min-height: 54px;
                box-sizing: border-box;
                padding: 6px 8px;
            }

            .fengjing-dropdown-thumbnail {
                width: 46px;
                height: 46px;
                flex: 0 0 46px;
                display: flex;
                align-items: center;
                justify-content: center;
                box-sizing: border-box;
                padding: 3px;
                overflow: hidden;
                border: 1px solid #e2e8f0;
                border-radius: 7px;
                background: #ffffff;
                cursor: zoom-in;
            }

            .fengjing-dropdown-thumbnail img {
                display: block;
                width: auto;
                height: auto;
                max-width: 100%;
                max-height: 100%;
                object-fit: contain;
                object-position: center center;
            }

            .fengjing-dropdown-thumbnail.is-empty {
                color: #94a3b8;
                background: #f8fafc;
                cursor: default;
            }

            .fengjing-dropdown-placeholder {
                font-size: 11px;
                line-height: 1;
                color: #94a3b8;
            }

            .fengjing-dropdown-content {
                min-width: 0;
                flex: 1;
                overflow: hidden;
                line-height: 1.4;
            }

            .fengjing-dropdown-content strong {
                display: block;
                overflow: hidden;
                color: #172033;
                font-size: 13px;
                font-weight: 600;
                white-space: nowrap;
                text-overflow: ellipsis;
            }

            .fengjing-dropdown-content .small {
                display: block;
                margin-top: 2px;
                overflow: hidden;
                color: #64748b;
                font-size: 12px;
                white-space: nowrap;
                text-overflow: ellipsis;
            }

            /* 物料下拉菜单中的批量选择入口 */
            .fengjing-multi-select-entry {
                position: sticky;
                top: 0;
                z-index: 4;
                display: block;
                margin: 0 0 6px !important;
                padding: 0 !important;
                border: 0 !important;
                background: #ffffff !important;
            }

            .fengjing-multi-select-button {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 7px;
                width: 100%;
                min-height: 38px;
                padding: 8px 12px;
                border: 1px solid #9db7ff;
                border-radius: 8px;
                color: #2356d8;
                font-size: 13px;
                font-weight: 600;
                background: linear-gradient(135deg, #f5f8ff 0%, #edf3ff 100%);
                cursor: pointer;
                transition: background-color 0.15s ease, border-color 0.15s ease;
            }

            .fengjing-multi-select-button:hover {
                border-color: #5f87ff;
                color: #1746c4;
                background: #e7efff;
            }

            /* 批量选择物料弹窗 */
            .fengjing-item-picker {
                min-height: 430px;
            }

            .fengjing-bom-picker {
                margin-bottom: 12px;
            }

            .fengjing-bom-picker-title {
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 7px;
                color: #475569;
                font-size: 12px;
                font-weight: 600;
            }

            .fengjing-bom-picker-list {
                display: flex;
                flex-direction: column;
                gap: 8px;
                max-height: min(58vh, 620px);
                padding: 2px 4px 6px 1px;
                overflow-y: auto;
                scrollbar-width: none;
            }

            .fengjing-bom-picker-list::-webkit-scrollbar {
                display: none;
            }

            .fengjing-bom-option {
                display: grid;
                grid-template-columns: 76px minmax(0, 1fr);
                align-items: center;
                gap: 8px;
                width: 100%;
                min-height: 84px;
                padding: 6px 8px;
                border: 1px solid #dce4ef;
                border-radius: 9px;
                color: #334155;
                background: #fff;
                cursor: pointer;
                transition: border-color .15s ease, background-color .15s ease,
                    box-shadow .15s ease;
            }

            .fengjing-bom-option:hover {
                border-color: #8daaf8;
                background: #f7f9ff;
            }

            .fengjing-bom-option.is-active {
                border-color: #4f78ed;
                background: #edf3ff;
                box-shadow: 0 0 0 1px rgba(79, 120, 237, .12);
            }

            .fengjing-bom-option-image {
                display: flex;
                align-items: center;
                justify-content: center;
                width: 76px;
                height: 76px;
                overflow: hidden;
                border-radius: 7px;
                color: #94a3b8;
                font-size: 10px;
                background: #f1f5f9;
            }

            .fengjing-bom-option-image img {
                width: 100%;
                height: 100%;
                object-fit: contain;
                background: #fff;
            }

            .fengjing-bom-option-info {
                min-width: 0;
            }

            .fengjing-bom-option-name,
            .fengjing-bom-option-code {
                display: block;
                overflow: hidden;
                white-space: nowrap;
                text-overflow: ellipsis;
            }

            .fengjing-bom-option-name {
                font-size: 12px;
                font-weight: 600;
            }

            .fengjing-bom-option-code {
                margin-top: 2px;
                color: #718096;
                font-size: 10px;
            }

            .fengjing-item-picker-toolbar {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 10px;
                margin-bottom: 10px;
                color: #64748b;
                font-size: 12px;
            }

            .fengjing-item-picker-list {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
                gap: 8px;
                max-height: min(58vh, 620px);
                overflow-y: auto;
                padding: 8px;
                border: 1px solid #dfe6f1;
                border-radius: 10px;
                background: #f8fafc;
            }

            .fengjing-item-picker-row {
                position: relative;
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 3px;
                aspect-ratio: 1 / 1;
                min-width: 0;
                min-height: 240px;
                padding: 8px;
                overflow: hidden;
                border: 1px solid #e1e7f0;
                border-radius: 10px;
                background: #fff;
                cursor: pointer;
                transition: transform .15s ease, border-color .15s ease,
                    box-shadow .15s ease, background-color .15s ease;
            }

            .fengjing-item-picker-row:hover,
            .fengjing-item-picker-row.is-selected {
                background: #f5f8ff;
                border-color: #7fa1fb;
                box-shadow: 0 4px 14px rgba(43, 79, 150, .10);
            }

            .fengjing-item-picker-row:hover {
                transform: translateY(-1px);
            }

            .fengjing-item-picker-image {
                display: flex;
                align-items: center;
                justify-content: center;
                width: 112px;
                height: 112px;
                flex: 0 0 auto;
                overflow: hidden;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                color: #94a3b8;
                font-size: 11px;
                background: #f8fafc;
            }

            .fengjing-item-picker-image img {
                width: 100%;
                height: 100%;
                object-fit: contain;
                background: #ffffff;
            }

            .fengjing-item-picker-info {
                display: block;
                width: 100%;
                min-width: 0;
                text-align: center;
            }

            .fengjing-item-picker-name {
                display: -webkit-box;
                overflow: hidden;
                min-height: 30px;
                line-height: 15px;
                -webkit-box-orient: vertical;
                -webkit-line-clamp: 2;
            }

            .fengjing-item-picker-name {
                color: #172033;
                font-size: 13px;
                font-weight: 600;
            }

            .fengjing-item-picker-code,
            .fengjing-item-picker-supplier {
                display: block;
                overflow: hidden;
                margin-top: 3px;
                color: #64748b;
                font-size: 11px;
                white-space: nowrap;
                text-overflow: ellipsis;
            }

            .fengjing-item-picker-stock {
                width: 100%;
                color: #334155;
                font-size: 11px;
                text-align: center;
            }

            .fengjing-item-picker-qty {
                width: 100%;
                height: 29px;
                margin-top: auto;
                padding: 4px 8px;
                border: 1px solid #cfd8e6;
                border-radius: 7px;
                text-align: center;
                background: #ffffff;
            }

            .fengjing-item-picker-empty {
                display: flex;
                align-items: center;
                justify-content: center;
                grid-column: 1 / -1;
                min-height: 240px;
                color: #94a3b8;
            }

            .fengjing-item-picker-pager {
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 10px;
                margin-top: 12px;
            }

            @media (max-width: 760px) {
                .fengjing-item-picker-list {
                    grid-template-columns: repeat(auto-fill, minmax(175px, 1fr));
                }

                .fengjing-item-picker-row {
                    min-height: 190px;
                }

                .fengjing-item-picker-image {
                    width: 90px;
                    height: 90px;
                }

                .fengjing-item-picker-qty {
                    width: 100%;
                }
            }
        `;

        document.head.appendChild(style);
    }

    // --------------------------------------------------------
    // 悬停大图
    // --------------------------------------------------------
    function 初始化大图预览() {
        if (document.getElementById("fengjing-item-image-preview")) {
            return;
        }

        const preview = document.createElement("div");
        preview.id = "fengjing-item-image-preview";

        const image = document.createElement("img");
        preview.appendChild(image);
        document.body.appendChild(preview);

        function 移动预览(event) {
            const 间距 = 18;
            const previewWidth = preview.offsetWidth || 480;
            const previewHeight = preview.offsetHeight || 480;

            let left = event.clientX + 间距;
            let top = event.clientY + 间距;

            if (left + previewWidth > window.innerWidth - 12) {
                left = event.clientX - previewWidth - 间距;
            }

            if (top + previewHeight > window.innerHeight - 12) {
                top = window.innerHeight - previewHeight - 12;
            }

            left = Math.max(12, left);
            top = Math.max(12, top);

            preview.style.left = `${left}px`;
            preview.style.top = `${top}px`;
        }

        document.addEventListener("mouseover", event => {
            if (!(event.target instanceof Element)) {
                return;
            }

            const source = event.target.closest(
                ".fengjing-image-hover-source"
            );

            if (!source) {
                return;
            }

            const imageUrl = source.dataset.imageUrl;

            if (!imageUrl) {
                return;
            }

            image.src = imageUrl;
            preview.style.display = "flex";
            移动预览(event);
        });

        document.addEventListener("mousemove", event => {
            if (preview.style.display === "flex") {
                移动预览(event);
            }
        });

        document.addEventListener("mouseout", event => {
            if (!(event.target instanceof Element)) {
                return;
            }

            const source = event.target.closest(
                ".fengjing-image-hover-source"
            );

            if (
                source &&
                !source.contains(event.relatedTarget)
            ) {
                preview.style.display = "none";
                image.removeAttribute("src");
            }
        });

        // 表格缩略图点击后显示遮罩大图；下拉菜单缩略图不会触发。
        const modal = document.createElement("div");
        modal.id = "fengjing-item-image-modal";

        const modalContent = document.createElement("div");
        modalContent.id = "fengjing-item-image-modal-content";

        const modalImage = document.createElement("img");
        modalContent.appendChild(modalImage);
        modal.appendChild(modalContent);
        document.body.appendChild(modal);

        function 关闭点击大图() {
            modal.classList.remove("is-open");
            modalImage.removeAttribute("src");
        }

        document.addEventListener("click", event => {
            if (!(event.target instanceof Element)) {
                return;
            }

            const tableThumbnail = event.target.closest(
                ".fengjing-table-thumbnail"
            );

            if (!tableThumbnail) {
                return;
            }

            const imageUrl = tableThumbnail.dataset.imageUrl;

            if (!imageUrl) {
                return;
            }

            event.preventDefault();
            event.stopPropagation();

            preview.style.display = "none";
            image.removeAttribute("src");
            modalImage.src = imageUrl;
            modal.classList.add("is-open");
        }, true);

        // 只有点击图片外面的黑色透明区域才关闭。
        modal.addEventListener("click", event => {
            if (event.target === modal) {
                关闭点击大图();
            }
        });

        // 点击已经放大的图片本身不会关闭。
        modalContent.addEventListener("click", event => {
            event.stopPropagation();
        });

        document.addEventListener("keydown", event => {
            if (event.key === "Escape" && modal.classList.contains("is-open")) {
                关闭点击大图();
            }
        });
    }

    // --------------------------------------------------------
    // items子表缩略图
    // --------------------------------------------------------
    function 设置表格物料缩略图(frm) {
        window.setTimeout(() => {
            const grid = frm.fields_dict.items?.grid;

            if (!grid || grid.__fengjing_thumbnail_enabled) {
                return;
            }

            const itemField = grid.docfields?.find(
                field => field.fieldname === "item_code"
            );

            if (!itemField) {
                return;
            }

            const itemFormatter = function (value, df, options, row) {
                const itemLink = frappe.form.formatters.Link(
                    value,
                    df,
                    options,
                    row
                );

                const imageUrl = row?.image;

                if (!imageUrl) {
                    return itemLink;
                }

                const safeImageUrl = frappe.utils.escape_html(
                    String(imageUrl)
                );

                return `
                    <div class="fengjing-item-cell">
                        <span
                            class="
                                fengjing-item-thumbnail
                                fengjing-image-hover-source
                                fengjing-table-thumbnail
                            "
                            data-image-url="${safeImageUrl}"
                        >
                            <img
                                src="${safeImageUrl}"
                                alt=""
                                loading="lazy"
                            >
                        </span>

                        <span class="fengjing-item-code">
                            ${itemLink}
                        </span>
                    </div>
                `;
            };

            itemField.formatter = itemFormatter;

            if (grid.fields_map?.item_code) {
                grid.fields_map.item_code.formatter = itemFormatter;
            }

            (grid.grid_rows || []).forEach(gridRow => {
                const rowField = gridRow.docfields?.find(
                    field => field.fieldname === "item_code"
                );

                if (rowField) {
                    rowField.formatter = itemFormatter;
                }
            });

            /*
             * 先设置标记，再刷新。
             * 不调用reset_grid，不修改子表结构。
             */
            grid.__fengjing_thumbnail_enabled = true;
            grid.refresh();
        }, 0);
    }

    支持的单据.forEach(doctype => {
        frappe.ui.form.on(doctype, {
            onload_post_render(frm) {
                设置表格物料缩略图(frm);
            },

            refresh(frm) {
                设置表格物料缩略图(frm);
            }
        });
    });

    // --------------------------------------------------------
    // Item下拉菜单图片
    // --------------------------------------------------------
    function 获取下拉选项数据(optionElement) {
        const domLibrary = window.jQuery || window.$;

        if (!domLibrary) {
            return null;
        }

        let optionData = domLibrary(optionElement).data(
            "item.autocomplete"
        );

        if (optionData) {
            return optionData;
        }

        const innerOption = optionElement.querySelector(
            '[role="option"]'
        );

        if (innerOption) {
            optionData = domLibrary(innerOption).data(
                "item.autocomplete"
            );
        }

        return optionData || null;
    }

    async function 查询缺失物料图片(itemCodes) {
        const missingCodes = itemCodes.filter(
            itemCode => !物料图片缓存.has(itemCode)
        );

        if (!missingCodes.length) {
            return;
        }

        try {
            const rows = await frappe.db.get_list("Item", {
                fields: ["name", "item_name", "image"],
                filters: {
                    name: ["in", missingCodes]
                },
                limit: missingCodes.length
            });

            const foundNames = new Set();

            (rows || []).forEach(row => {
                foundNames.add(row.name);

                物料图片缓存.set(row.name, {
                    image: row.image || "",
                    item_name: row.item_name || ""
                });
            });

            // 查询不到或没有权限的也做缓存，避免反复请求
            missingCodes.forEach(itemCode => {
                if (!foundNames.has(itemCode)) {
                    物料图片缓存.set(itemCode, {
                        image: "",
                        item_name: ""
                    });
                }
            });
        } catch (error) {
            console.warn("读取物料下拉图片失败：", error);
        }
    }

    function 美化单个下拉选项(optionElement, itemCode) {
        const paragraph = optionElement.querySelector("p");

        if (!paragraph) {
            return;
        }

        const itemData = 物料图片缓存.get(itemCode) || {};
        const imageUrl = itemData.image || "";

        let wrapper = paragraph.querySelector(
            ".fengjing-dropdown-item"
        );

        if (!wrapper) {
            wrapper = document.createElement("span");
            wrapper.className = "fengjing-dropdown-item";

            const thumbnail = document.createElement("span");
            thumbnail.className = "fengjing-dropdown-thumbnail";

            const content = document.createElement("span");
            content.className = "fengjing-dropdown-content";

            while (paragraph.firstChild) {
                content.appendChild(paragraph.firstChild);
            }

            wrapper.appendChild(thumbnail);
            wrapper.appendChild(content);
            paragraph.appendChild(wrapper);
        }

        const thumbnail = wrapper.querySelector(
            ".fengjing-dropdown-thumbnail"
        );

        if (!thumbnail) {
            return;
        }

        thumbnail.replaceChildren();
        thumbnail.classList.remove(
            "is-empty",
            "fengjing-image-hover-source"
        );
        thumbnail.removeAttribute("data-image-url");

        if (imageUrl) {
            const image = document.createElement("img");
            image.src = imageUrl;
            image.alt = "";
            image.loading = "lazy";

            image.addEventListener("error", () => {
                thumbnail.classList.add("is-empty");
                thumbnail.classList.remove(
                    "fengjing-image-hover-source"
                );
                thumbnail.removeAttribute("data-image-url");
                thumbnail.innerHTML =
                    '<span class="fengjing-dropdown-placeholder">无图</span>';
            });

            thumbnail.appendChild(image);
            thumbnail.classList.add(
                "fengjing-image-hover-source"
            );
            thumbnail.dataset.imageUrl = imageUrl;
        } else {
            thumbnail.classList.add("is-empty");
            thumbnail.innerHTML =
                '<span class="fengjing-dropdown-placeholder">无图</span>';
        }
    }

    // --------------------------------------------------------
    // 批量选择物料
    // --------------------------------------------------------
    function 获取当前物料表单(input) {
        const frm = window.cur_frm;

        if (
            !frm ||
            !支持的单据.includes(frm.doc.doctype) ||
            frm.doc.docstatus !== 0
        ) {
            return null;
        }

        const grid = frm.fields_dict.items?.grid;
        const wrapper = grid?.wrapper?.get
            ? grid.wrapper.get(0)
            : grid?.wrapper;

        if (!grid || !wrapper || !wrapper.contains(input)) {
            return null;
        }

        return frm;
    }

    async function 批量填充物料(frm, selectedItems) {
        const selections = Array.from(selectedItems.values()).filter(
            item => Number(item.qty) > 0
        );

        if (!selections.length) {
            frappe.msgprint(__("请至少选择一个物料并填写有效数量。"));
            return false;
        }

        if (selections.length > 100) {
            frappe.msgprint(__("每次最多批量添加100个物料。"));
            return false;
        }

        frappe.dom.freeze(__("正在按顺序填充物料资料…"));

        try {
            const response = await frappe.call({
                method: "fengjing_app.api.item_multi_select.get_items_by_codes",
                args: {
                    item_codes: selections.map(item => item.item_code)
                }
            });

            const resolvedItems = new Map(
                (response.message || []).map(item => [
                    String(item.item_code || ""),
                    item
                ])
            );
            const grid = frm.fields_dict.items?.grid;

            if (!grid) {
                throw new Error(__("当前单据没有可填充的物料明细表。"));
            }

            let addedCount = 0;
            let mergedCount = 0;

            for (const selected of selections) {
                const resolvedItem = resolvedItems.get(selected.item_code);
                if (!resolvedItem) {
                    continue;
                }

                // 始终使用服务器重新核验后的物料号，物料套件只负责筛选，
                // 绝不能把组件替换为套件父物料。
                const exactItemCode = String(resolvedItem.item_code || "");

                const qty = Number(selected.qty);
                const existingRow = (frm.doc.items || []).find(
                    row => row.item_code === exactItemCode
                );

                if (existingRow) {
                    await frappe.model.set_value(
                        existingRow.doctype,
                        existingRow.name,
                        "qty",
                        Number(existingRow.qty || 0) + qty
                    );
                    mergedCount += 1;
                    continue;
                }

                let row = (frm.doc.items || []).find(
                    item => !item.item_code
                );

                if (!row) {
                    row = frm.add_child("items");
                }

                // 使用 ERPNext 原生事件写入，并等待它触发的后台请求
                // 全部结束后再处理下一行，避免多行资料相互覆盖。
                await frappe.model.set_value(
                    row.doctype,
                    row.name,
                    "item_code",
                    exactItemCode
                );
                await new Promise(resolve => frappe.after_ajax(resolve));

                // 后台处理完成后核对实际写入值；发生错位时立即停止，
                // 不再通过二次 set_value 制造新的异步请求。
                const processedRow = frappe.get_doc(row.doctype, row.name) || row;
                if (String(processedRow.item_code || "") !== exactItemCode) {
                    throw new Error(__(
                        "物料 {0} 被其他脚本改成了 {1}，本次已停止填充。",
                        [exactItemCode, processedRow.item_code || __("空值")]
                    ));
                }
                await frappe.model.set_value(
                    processedRow.doctype,
                    processedRow.name,
                    "qty",
                    qty
                );
                await new Promise(resolve => frappe.after_ajax(resolve));
                addedCount += 1;
            }

            frm.refresh_field("items");
            frm.dirty();
            frappe.show_alert({
                message: __(
                    "批量添加完成：新增 {0} 项，合并 {1} 项",
                    [addedCount, mergedCount]
                ),
                indicator: "green"
            });
            return true;
        } catch (error) {
            console.error("批量填充物料失败：", error);
            frappe.msgprint({
                title: __("批量添加失败"),
                message: frappe.utils.escape_html(
                    error?.message || String(error)
                ),
                indicator: "red"
            });
            return false;
        } finally {
            frappe.dom.unfreeze();
        }
    }

    function 打开物料多选窗口(frm) {
        const state = {
            start: 0,
            pageLength: 80,
            hasMore: false,
            loading: false,
            bundleLoading: false,
            items: [],
            bundles: [],
            selectedBundle: "",
            selected: new Map()
        };

        const dialog = new frappe.ui.Dialog({
            title: __("批量选择物料"),
            size: "extra-large",
            fields: [
                {
                    fieldname: "bundle_search_text",
                    fieldtype: "Data",
                    label: __("搜索物料套件"),
                    placeholder: __("输入套件、父物料编号或名称")
                },
                {
                    fieldname: "bundle_filter",
                    fieldtype: "HTML"
                },
                {
                    fieldtype: "Column Break"
                },
                {
                    fieldname: "search_text",
                    fieldtype: "Data",
                    label: __("搜索物料"),
                    placeholder: __("输入物料号、名称或物料组")
                },
                {
                    fieldname: "item_list",
                    fieldtype: "HTML"
                }
            ],
            primary_action_label: __("确定添加"),
            async primary_action() {
                const success = await 批量填充物料(
                    frm,
                    state.selected
                );
                if (success) {
                    dialog.hide();
                }
            }
        });

        const bundleWrapper = dialog.fields_dict.bundle_filter.$wrapper.get(0);
        const listWrapper = dialog.fields_dict.item_list.$wrapper.get(0);

        function 渲染物料套件列表() {
            if (state.bundleLoading) {
                bundleWrapper.innerHTML = `
                    <div class="fengjing-bom-picker">
                        <div class="fengjing-bom-picker-title">
                            <span>${__("物料套件")}</span>
                            <span>${__("正在加载…")}</span>
                        </div>
                    </div>
                `;
                return;
            }

            const bundleCards = state.bundles.map((bundle, index) => {
                const safeName = frappe.utils.escape_html(
                    String(bundle.name || "")
                );
                const safeItemName = frappe.utils.escape_html(
                    String(bundle.item_name || bundle.item || bundle.name || "")
                );
                const safeImage = frappe.utils.escape_html(
                    String(bundle.image || "")
                );
                const imageHtml = safeImage
                    ? `<img src="${safeImage}" alt="" loading="lazy">`
                    : __("套件");

                return `
                    <button
                        type="button"
                        class="fengjing-bom-option ${
                            state.selectedBundle === bundle.name ? "is-active" : ""
                        }"
                        data-bom-index="${index}"
                        title="${safeName}"
                    >
                        <span class="fengjing-bom-option-image">${imageHtml}</span>
                        <span class="fengjing-bom-option-info">
                            <span class="fengjing-bom-option-name">${safeItemName}</span>
                            <span class="fengjing-bom-option-code">${safeName}</span>
                        </span>
                    </button>
                `;
            }).join("");

            bundleWrapper.innerHTML = `
                <div class="fengjing-bom-picker">
                    <div class="fengjing-bom-picker-title">
                        <span>${__("物料套件")}</span>
                        <span>${__("选择后仅显示该套件的组件")}</span>
                    </div>
                    <div class="fengjing-bom-picker-list">
                        <button
                            type="button"
                            class="fengjing-bom-option ${
                                state.selectedBundle ? "" : "is-active"
                            }"
                            data-bom-index="-1"
                        >
                            <span class="fengjing-bom-option-image">${__("全部")}</span>
                            <span class="fengjing-bom-option-info">
                                <span class="fengjing-bom-option-name">${__("全部物料")}</span>
                                <span class="fengjing-bom-option-code">${__("取消套件筛选")}</span>
                            </span>
                        </button>
                        ${bundleCards || `
                            <span class="text-muted small">
                                ${__("暂无已启用的物料套件")}
                            </span>
                        `}
                    </div>
                </div>
            `;

            bundleWrapper.querySelectorAll(".fengjing-bom-option").forEach(
                element => {
                    element.addEventListener("click", () => {
                        const index = Number(element.dataset.bomIndex);
                        state.selectedBundle = index >= 0
                            ? String(state.bundles[index]?.name || "")
                            : "";
                        state.start = 0;
                        渲染物料套件列表();
                        加载物料();
                    });
                }
            );
        }

        async function 加载物料套件列表() {
            state.bundleLoading = true;
            渲染物料套件列表();
            try {
                const response = await frappe.call({
                    method: "fengjing_app.api.item_multi_select.get_product_bundle_options",
                    args: {
                        txt: dialog.get_value("bundle_search_text") || "",
                        page_length: 100
                    }
                });
                state.bundles = response.message || [];
            } catch (error) {
                console.error("加载物料套件列表失败：", error);
                state.bundles = [];
                frappe.show_alert({
                    message: __("物料套件列表加载失败"),
                    indicator: "orange"
                });
            } finally {
                state.bundleLoading = false;
                渲染物料套件列表();
            }
        }

        function 刷新已选数量() {
            const element = listWrapper.querySelector(
                ".fengjing-item-picker-selected-count"
            );
            if (element) {
                element.textContent = __("已选 {0} 项", [
                    state.selected.size
                ]);
            }
        }

        function 渲染物料列表() {
            if (state.loading) {
                listWrapper.innerHTML = `
                    <div class="fengjing-item-picker">
                        <div class="fengjing-item-picker-empty">
                            ${__("正在加载物料…")}
                        </div>
                    </div>
                `;
                return;
            }

            const rows = state.items.map(item => {
                const code = String(item.item_code || "");
                const selected = state.selected.get(code);
                const safeCode = frappe.utils.escape_html(code);
                const safeName = frappe.utils.escape_html(
                    String(item.item_name || code)
                );
                const safeImage = frappe.utils.escape_html(
                    String(item.image || "")
                );
                const safeSupplier = frappe.utils.escape_html(
                    String(item.default_supplier || __("未设默认供应商"))
                );
                const stockQty = format_number(
                    Number(item.actual_qty || 0),
                    null,
                    2
                );
                const safeUom = frappe.utils.escape_html(
                    String(item.stock_uom || "")
                );
                const bundleQty = Number(item.bundle_qty || 0);
                const imageHtml = safeImage
                    ? `<img src="${safeImage}" alt="" loading="lazy">`
                    : __("无图");

                return `
                    <div
                        class="fengjing-item-picker-row ${
                            selected ? "is-selected" : ""
                        }"
                        data-item-code="${safeCode}"
                    >
                        <span class="fengjing-item-picker-image">
                            ${imageHtml}
                        </span>
                        <span class="fengjing-item-picker-info">
                            <span class="fengjing-item-picker-name" title="${safeName}">
                                ${safeName}
                            </span>
                            <span class="fengjing-item-picker-code" title="${safeCode}">
                                ${safeCode}
                            </span>
                            <span class="fengjing-item-picker-supplier">
                                ${safeSupplier}
                            </span>
                        </span>
                        <span class="fengjing-item-picker-stock">
                            ${__("库存")} ${stockQty} ${safeUom}
                            ${bundleQty > 0 ? ` · ${__("套件用量")} ${format_number(bundleQty, null, 3)}` : ""}
                        </span>
                        <input
                            type="number"
                            class="fengjing-item-picker-qty"
                            min="0.000001"
                            step="any"
                            value="${selected?.qty ?? (bundleQty || 1)}"
                            aria-label="${__("数量")}"
                        >
                    </div>
                `;
            }).join("");

            listWrapper.innerHTML = `
                <div class="fengjing-item-picker">
                    <div class="fengjing-item-picker-toolbar">
                        <span>${__("点击卡片选择；蓝色表示已选，可直接修改数量")}</span>
                        <strong class="fengjing-item-picker-selected-count">
                            ${__("已选 {0} 项", [state.selected.size])}
                        </strong>
                    </div>
                    <div class="fengjing-item-picker-list">
                        ${rows || `
                            <div class="fengjing-item-picker-empty">
                                ${__("没有找到符合条件的物料")}
                            </div>
                        `}
                    </div>
                    <div class="fengjing-item-picker-pager">
                        <button
                            type="button"
                            class="btn btn-default btn-sm fengjing-item-picker-prev"
                            ${state.start <= 0 ? "disabled" : ""}
                        >${__("上一页")}</button>
                        <span>${__("第 {0} 页", [
                            Math.floor(state.start / state.pageLength) + 1
                        ])}</span>
                        <button
                            type="button"
                            class="btn btn-default btn-sm fengjing-item-picker-next"
                            ${state.hasMore ? "" : "disabled"}
                        >${__("下一页")}</button>
                    </div>
                </div>
            `;

            listWrapper.querySelectorAll(
                ".fengjing-item-picker-row"
            ).forEach(rowElement => {
                const code = rowElement.dataset.itemCode;
                const item = state.items.find(
                    current => current.item_code === code
                );
                const qtyInput = rowElement.querySelector(
                    ".fengjing-item-picker-qty"
                );

                function 设置选中(checked) {
                    const qty = Math.max(Number(qtyInput.value || 1), 0.000001);
                    rowElement.classList.toggle("is-selected", checked);
                    if (checked) {
                        state.selected.set(code, {
                            item_code: code,
                            item_name: item?.item_name || code,
                            qty
                        });
                    } else {
                        state.selected.delete(code);
                    }
                    刷新已选数量();
                }

                rowElement.addEventListener("click", event => {
                    if (event.target === qtyInput) {
                        return;
                    }
                    设置选中(!state.selected.has(code));
                });

                qtyInput.addEventListener("input", () => {
                    if (state.selected.has(code)) {
                        const selected = state.selected.get(code);
                        if (selected) {
                            selected.qty = Math.max(
                                Number(qtyInput.value || 1),
                                0.000001
                            );
                        }
                    }
                });
            });

            listWrapper.querySelector(
                ".fengjing-item-picker-prev"
            )?.addEventListener("click", () => {
                state.start = Math.max(0, state.start - state.pageLength);
                加载物料();
            });

            listWrapper.querySelector(
                ".fengjing-item-picker-next"
            )?.addEventListener("click", () => {
                if (state.hasMore) {
                    state.start += state.pageLength;
                    加载物料();
                }
            });
        }

        async function 加载物料() {
            if (state.loading) {
                return;
            }

            state.loading = true;
            渲染物料列表();

            try {
                const response = await frappe.call({
                    method: "fengjing_app.api.item_multi_select.search_items",
                    args: {
                        doctype: frm.doc.doctype,
                        txt: dialog.get_value("search_text") || "",
                        start: state.start,
                        page_length: state.pageLength,
                        company: frm.doc.company || "",
                        product_bundle: state.selectedBundle || ""
                    }
                });
                const data = response.message || {};
                state.items = data.items || [];
                state.hasMore = Boolean(data.has_more);
            } catch (error) {
                console.error("加载批量物料失败：", error);
                state.items = [];
                state.hasMore = false;
                frappe.show_alert({
                    message: __("物料列表加载失败"),
                    indicator: "red"
                });
            } finally {
                state.loading = false;
                渲染物料列表();
            }
        }

        const searchInput = dialog.fields_dict.search_text.$input;
        const bundleSearchInput = dialog.fields_dict.bundle_search_text.$input;
        let searchTimer = null;
        let bundleSearchTimer = null;
        searchInput.on("input", () => {
            window.clearTimeout(searchTimer);
            searchTimer = window.setTimeout(() => {
                state.start = 0;
                加载物料();
            }, 280);
        });

        searchInput.on("keydown", event => {
            if (event.key === "Enter") {
                event.preventDefault();
                window.clearTimeout(searchTimer);
                state.start = 0;
                加载物料();
            }
        });

        bundleSearchInput.on("input", () => {
            window.clearTimeout(bundleSearchTimer);
            bundleSearchTimer = window.setTimeout(() => {
                加载物料套件列表();
            }, 280);
        });

        bundleSearchInput.on("keydown", event => {
            if (event.key === "Enter") {
                event.preventDefault();
                window.clearTimeout(bundleSearchTimer);
                加载物料套件列表();
            }
        });

        dialog.show();
        const leftColumn = $(bundleWrapper).closest(".form-column");
        const rightColumn = $(listWrapper).closest(".form-column");
        leftColumn.removeClass("col-sm-6").addClass("col-sm-3");
        rightColumn.removeClass("col-sm-6").addClass("col-sm-9");
        加载物料套件列表();
        加载物料();
    }

    function 确保批量选择入口(input, dropdown, awesompleteInstance) {
        const frm = 获取当前物料表单(input);

        if (!frm) {
            dropdown.querySelector(
                ".fengjing-multi-select-entry"
            )?.remove();
            return;
        }

        if (dropdown.querySelector(".fengjing-multi-select-entry")) {
            return;
        }

        const entry = document.createElement("li");
        entry.className = "fengjing-multi-select-entry";
        entry.setAttribute("role", "presentation");
        entry.innerHTML = `
            <button type="button" class="fengjing-multi-select-button">
                <span aria-hidden="true">☑</span>
                <span>${__("批量选择物料")}</span>
            </button>
        `;

        const button = entry.querySelector("button");
        button.addEventListener("mousedown", event => {
            event.preventDefault();
            event.stopPropagation();
            awesompleteInstance?.close?.();
            input.blur();
            window.setTimeout(() => 打开物料多选窗口(frm), 0);
        });
        button.addEventListener("click", event => {
            event.preventDefault();
            event.stopPropagation();
        });

        // 必须放在原生候选项末尾。放在最前面会令 Awesomplete 的
        // DOM 序号与 suggestions 序号错位：界面点 000039，实际可能
        // 选择 000034 或 000027。
        dropdown.append(entry);
    }

    async function 美化物料下拉菜单(input) {
        // 直接取得Frappe正在使用的Awesomplete实例。下拉选项的
        // 物料编号优先从instance.suggestions读取，不再依赖DOM数据。
        const awesompleteInstance = Array.from(
            window.Awesomplete?.all || []
        ).find(instance => instance.input === input);

        const awesomplete =
            awesompleteInstance?.container ||
            input.closest(".awesomplete");

        if (!awesomplete) {
            return;
        }

        const dropdown =
            awesompleteInstance?.ul ||
            awesomplete.querySelector("ul");

        if (!dropdown) {
            return;
        }

        确保批量选择入口(
            input,
            dropdown,
            awesompleteInstance
        );

        // 使用直接子项，兼容不同版本的Awesomplete结构并避免重复。
        const options = Array.from(dropdown.children);

        const validOptions = [];
        const itemCodes = [];

        let suggestionIndex = 0;

        options.forEach(optionElement => {
            if (optionElement.classList.contains("fengjing-multi-select-entry")) {
                return;
            }

            const optionData = 获取下拉选项数据(optionElement);
            const suggestion =
                awesompleteInstance?.suggestions?.[suggestionIndex];

            suggestionIndex += 1;

            const itemCode =
                optionData?.value ||
                suggestion?.value ||
                "";

            // 排除“高级搜索”等功能选项
            if (
                !itemCode ||
                itemCode === "advanced_search__link_option" ||
                itemCode === "filter_description__link_option"
            ) {
                return;
            }

            const displayElement = optionElement.matches('[role="option"]')
                ? optionElement
                : optionElement.querySelector('[role="option"]') || optionElement;

            validOptions.push({
                element: displayElement,
                itemCode
            });

            itemCodes.push(itemCode);
        });

        if (!itemCodes.length) {
            return;
        }

        const uniqueCodes = [...new Set(itemCodes)];
        const currentKey = uniqueCodes.join("|");

        if (
            dropdown.dataset.fengjingProcessedKey === currentKey &&
            validOptions.every(item =>
                item.element.querySelector(
                    ".fengjing-dropdown-item"
                )
            )
        ) {
            return;
        }

        dropdown.dataset.fengjingProcessedKey = currentKey;
        dropdown.classList.add("fengjing-item-dropdown");

        await 查询缺失物料图片(uniqueCodes);

        // 用户快速输入时，旧的异步请求可能已经失效
        if (!dropdown.isConnected) {
            return;
        }

        validOptions.forEach(item => {
            美化单个下拉选项(
                item.element,
                item.itemCode
            );
        });
    }

    let 扫描计时器 = null;

    function 安排扫描物料下拉菜单() {
        window.clearTimeout(扫描计时器);

        扫描计时器 = window.setTimeout(() => {
            document
                .querySelectorAll(
                    'input[data-target="Item"]'
                )
                .forEach(input => {
                    const awesompleteInstance = Array.from(
                        window.Awesomplete?.all || []
                    ).find(instance => instance.input === input);

                    const awesomplete =
                        awesompleteInstance?.container ||
                        input.closest(".awesomplete");

                    const dropdown =
                        awesompleteInstance?.ul ||
                        awesomplete?.querySelector("ul");

                    const isOpen = awesompleteInstance
                        ? Boolean(awesompleteInstance.isOpened)
                        : Boolean(
                            dropdown &&
                            !dropdown.hasAttribute("hidden")
                        );

                    if (
                        dropdown &&
                        isOpen
                    ) {
                        美化物料下拉菜单(input);
                    }
                });
        }, 60);
    }

    const dropdownObserver = new MutationObserver(() => {
        安排扫描物料下拉菜单();
    });

    // Frappe每次打开或重新计算下拉结果时都会触发该事件。
    document.addEventListener(
        "awesomplete-open",
        event => {
            const input = event.target;

            if (
                input instanceof HTMLInputElement &&
                input.dataset.target === "Item"
            ) {
                window.setTimeout(() => {
                    美化物料下拉菜单(input);
                }, 0);
            }
        },
        true
    );

    function 初始化模块() {
        添加样式();
        初始化大图预览();

        dropdownObserver.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: [
                "hidden",
                "class",
                "aria-selected"
            ]
        });
    }

    if (document.body) {
        初始化模块();
    } else {
        document.addEventListener(
            "DOMContentLoaded",
            初始化模块,
            { once: true }
        );
    }
})();
