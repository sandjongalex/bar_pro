(function () {
  "use strict";

  var root = document.getElementById("catalogManager");
  if (!root) return;

  var csrfField = root.querySelector("[data-catalog-csrf]");
  if (!csrfField) return;

  var csrfToken = csrfField.value;
  var productsUrl = root.getAttribute("data-products-url");
  var productDeleteUrl = root.getAttribute("data-product-delete-url");
  var categoriesUrl = root.getAttribute("data-categories-url");
  var tbody = document.getElementById("catalogProductRows");
  var productTemplate = document.getElementById("catalogNewProductTemplate");
  var notice = document.getElementById("catalogNotice");
  var categoryList = document.getElementById("catalogCategoryList");
  var searchInput = document.getElementById("catalogSearch");
  var categoryFilter = document.getElementById("catalogCategoryFilter");
  var activeFilter = document.getElementById("catalogActiveFilter");

  if (!tbody || !productsUrl || !categoriesUrl) return;

  function showNotice(message, isError) {
    if (!notice) return;
    notice.textContent = message || "";
    notice.className = "catalog-notice alert " + (isError ? "alert-danger" : "alert-success") + " is-visible";
  }

  function hideNotice() {
    if (!notice) return;
    notice.textContent = "";
    notice.className = "catalog-notice alert";
  }

  function request(url, formData) {
    formData.append("csrf_token", csrfToken);
    var headers = {
      "Accept": "application/json",
      "X-Requested-With": "XMLHttpRequest",
      "X-BarPro-Async": "1"
    };

    if (window.fetch) {
      return window.fetch(url, {
        method: "POST",
        credentials: "same-origin",
        body: formData,
        headers: headers
      }).then(function (response) {
        return response.text().then(function (body) {
          var payload;
          try {
            payload = JSON.parse(body);
          } catch (error) {
            throw new Error("Réponse inattendue. Vérifiez votre session avant de réessayer.");
          }
          if (!response.ok || !payload.success) {
            throw new Error(payload.message || "Enregistrement refusé.");
          }
          return payload;
        });
      });
    }

    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", url, true);
      xhr.withCredentials = true;
      xhr.setRequestHeader("Accept", headers.Accept);
      xhr.setRequestHeader("X-Requested-With", headers["X-Requested-With"]);
      xhr.setRequestHeader("X-BarPro-Async", headers["X-BarPro-Async"]);
      xhr.onreadystatechange = function () {
        if (xhr.readyState !== 4) return;
        var payload;
        try {
          payload = JSON.parse(xhr.responseText || "{}");
        } catch (error) {
          reject(new Error("Réponse inattendue. Vérifiez votre session avant de réessayer."));
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300 || !payload.success) {
          reject(new Error(payload.message || "Enregistrement refusé."));
          return;
        }
        resolve(payload);
      };
      xhr.onerror = function () {
        reject(new Error("Connexion interrompue. Vérifiez votre connexion avant de réessayer."));
      };
      xhr.send(formData);
    });
  }

  function rowFeedback(row, message, isError) {
    var target = row.querySelector("[data-row-feedback]");
    if (!target) return;
    target.textContent = message || "";
    target.className = "catalog-row-feedback" + (isError ? " is-error" : "");
  }

  function setBusy(button, busy, label) {
    if (!button) return;
    if (busy) {
      button.disabled = true;
      button.setAttribute("data-original-label", button.textContent);
      button.textContent = label || "Enregistrement…";
    } else {
      button.disabled = false;
      var original = button.getAttribute("data-original-label");
      if (original) button.textContent = original;
      button.removeAttribute("data-original-label");
    }
  }

  function selectedValue(row, fieldName) {
    var field = row.querySelector('[data-field="' + fieldName + '"]');
    return field ? field.value : "";
  }

  function collectProductForm(row) {
    var form = new FormData();
    var fields = row.querySelectorAll("[data-field]");
    Array.prototype.forEach.call(fields, function (field) {
      var name = field.getAttribute("data-field");
      if (!name) return;
      if (field.type === "file") {
        if (field.files && field.files.length) form.append("image", field.files[0]);
      } else if (field.type === "checkbox") {
        if (field.checked) form.append(name, field.value || "yes");
      } else {
        form.append(name, field.value);
      }
    });

    if (row.classList.contains("catalog-new-product-row")) {
      form.append("insert_before_id", row.getAttribute("data-before-id") || "");
    } else {
      form.append("product_id", row.getAttribute("data-product-id") || "");
    }
    return form;
  }

  function updateProductImage(row, imageUrl) {
    var wrap = row.querySelector(".catalog-product-thumb-wrap");
    if (!wrap) return;
    while (wrap.firstChild) wrap.removeChild(wrap.firstChild);

    if (imageUrl) {
      var image = document.createElement("img");
      image.className = "catalog-product-thumb";
      image.src = imageUrl;
      image.alt = selectedValue(row, "name") || "Photo du produit";
      image.loading = "lazy";
      wrap.appendChild(image);
    } else {
      var avatar = document.createElement("span");
      avatar.className = "catalog-product-avatar";
      avatar.textContent = (selectedValue(row, "name") || "P").charAt(0).toUpperCase();
      wrap.appendChild(avatar);
    }
  }

  function makeStockBadge(quantity, threshold) {
    var badge = document.createElement("span");
    badge.className = "catalog-stock-pill";
    badge.textContent = quantity === undefined || quantity === null ? "0" : String(quantity);
    var q = Number(quantity || 0);
    var t = Number(threshold || 0);
    if (q <= t) badge.className += " is-low";
    return badge;
  }

  function refreshStats() {
    var rows = Array.prototype.slice.call(tbody.querySelectorAll(".catalog-product-row"));
    var activeProducts = rows.filter(function (row) {
      return row.getAttribute("data-active") === "true";
    });
    var lowStock = rows.filter(function (row) {
      var quantity = Number(row.getAttribute("data-quantity") || 0);
      var threshold = Number(selectedValue(row, "stock_alert_threshold") || 0);
      return quantity <= threshold;
    });
    var categories = Array.prototype.slice.call(categoryList ? categoryList.querySelectorAll(".catalog-category-row") : []);
    var activeCategories = categories.filter(function (row) {
      return row.getAttribute("data-active") === "true";
    });

    var productStat = root.querySelector('[data-stat="products"]');
    var categoryStat = root.querySelector('[data-stat="categories"]');
    var lowStat = root.querySelector('[data-stat="low-stock"]');
    var allStat = root.querySelector('[data-stat="all-products"]');
    if (productStat) productStat.textContent = String(activeProducts.length);
    if (categoryStat) categoryStat.textContent = String(activeCategories.length);
    if (lowStat) lowStat.textContent = String(lowStock.length);
    if (allStat) allStat.textContent = String(rows.length);

    var addButton = document.getElementById("addCatalogProduct");
    if (addButton) addButton.disabled = activeCategories.length === 0;

    var onboarding = document.getElementById("catalogOnboarding");
    if (onboarding && activeCategories.length) onboarding.style.display = "none";
  }

  function hasActiveFilters() {
    return !!(
      (searchInput && searchInput.value.trim()) ||
      (categoryFilter && categoryFilter.value) ||
      (activeFilter && activeFilter.value)
    );
  }

  function applyFilters() {
    var query = searchInput ? searchInput.value.trim().toLocaleLowerCase() : "";
    var categoryId = categoryFilter ? categoryFilter.value : "";
    var active = activeFilter ? activeFilter.value : "";
    var rows = tbody.querySelectorAll(".catalog-product-row");

    Array.prototype.forEach.call(rows, function (row) {
      var name = selectedValue(row, "name").toLocaleLowerCase();
      var sku = selectedValue(row, "sku").toLocaleLowerCase();
      var categoryMatch = !categoryId || row.getAttribute("data-category-id") === categoryId;
      var activeMatch = !active || row.getAttribute("data-active") === active;
      var textMatch = !query || name.indexOf(query) !== -1 || sku.indexOf(query) !== -1;
      row.hidden = !(categoryMatch && activeMatch && textMatch);
    });

    var insertButtons = root.querySelectorAll('[data-action="insert-after"]');
    Array.prototype.forEach.call(insertButtons, function (button) {
      button.hidden = hasActiveFilters();
    });
  }

  function clearFilters() {
    if (searchInput) searchInput.value = "";
    if (categoryFilter) categoryFilter.value = "";
    if (activeFilter) activeFilter.value = "";
    applyFilters();
  }

  function addNewProductRow(anchor, beforeId) {
    if (!productTemplate || !productTemplate.content || !productTemplate.content.firstElementChild) {
      showNotice("Le formulaire d'ajout n'est pas disponible.", true);
      return;
    }
    var existing = tbody.querySelector(".catalog-new-product-row");
    if (existing) {
      var existingName = existing.querySelector('[data-field="name"]');
      if (existingName) existingName.focus();
      return;
    }
    var row = productTemplate.content.firstElementChild.cloneNode(true);
    row.setAttribute("data-before-id", beforeId || "");
    if (anchor && anchor.parentNode === tbody) {
      anchor.parentNode.insertBefore(row, anchor.nextSibling);
    } else {
      tbody.appendChild(row);
    }
    var nameField = row.querySelector('[data-field="name"]');
    if (nameField) nameField.focus();
    if (row.scrollIntoView) row.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function saveProduct(row, button) {
    var isNew = row.classList.contains("catalog-new-product-row");
    var requiredFields = row.querySelectorAll("[required]");
    for (var i = 0; i < requiredFields.length; i += 1) {
      if (!requiredFields[i].checkValidity()) {
        requiredFields[i].reportValidity();
        return;
      }
    }

    hideNotice();
    setBusy(button, true, "Enregistrement…");
    request(productsUrl, collectProductForm(row)).then(function (payload) {
      var data = payload.data || {};
      row.setAttribute("data-category-id", String(data.category_id || selectedValue(row, "category_id")));
      row.setAttribute("data-active", data.is_active ? "true" : "false");
      row.setAttribute("data-quantity", String(data.quantity || "0"));
      updateProductImage(row, data.image_url || null);

      if (isNew) {
        row.classList.remove("catalog-new-product-row");
        row.classList.add("catalog-product-row");
        row.setAttribute("data-product-id", String(data.id));
        row.removeAttribute("data-before-id");

        var initialQuantity = row.querySelector('[data-field="initial_quantity"]');
        if (initialQuantity) initialQuantity.remove();
        var stockCell = row.querySelector("[data-stock-cell]");
        if (stockCell) {
          while (stockCell.firstChild) stockCell.removeChild(stockCell.firstChild);
          stockCell.appendChild(makeStockBadge(data.quantity || "0", selectedValue(row, "stock_alert_threshold")));
        }

        var createButton = row.querySelector('[data-action="create-product"]');
        if (createButton) {
          createButton.setAttribute("data-action", "save-product");
          createButton.textContent = "Enregistrer";
          createButton.disabled = false;
          createButton.removeAttribute("data-original-label");
        }
        var cancelButton = row.querySelector('[data-action="cancel-new"]');
        if (cancelButton && cancelButton.parentNode) cancelButton.parentNode.removeChild(cancelButton);

        var insertButton = document.createElement("button");
        insertButton.type = "button";
        insertButton.className = "btn btn-sm btn-light border";
        insertButton.setAttribute("data-action", "insert-after");
        insertButton.title = "Insérer un produit après celui-ci";
        insertButton.textContent = "＋";
        var actions = row.querySelector("[data-actions]");
        if (actions) {
          actions.appendChild(insertButton);
          var deleteButton = document.createElement("button");
          deleteButton.type = "button";
          deleteButton.className = "btn btn-sm btn-outline-danger";
          deleteButton.setAttribute("data-action", "delete-product");
          deleteButton.title = "Supprimer ou archiver ce produit";
          deleteButton.textContent = "Supprimer";
          actions.appendChild(deleteButton);
        }

        var empty = document.getElementById("catalogEmpty");
        if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
      }

      var activeField = row.querySelector('[data-field="is_active"]');
      if (activeField) {
        if (isNew && activeField.options.length < 2) {
          var archivedOption = document.createElement("option");
          archivedOption.value = "false";
          archivedOption.textContent = "Archivé";
          activeField.appendChild(archivedOption);
        }
        activeField.value = data.is_active ? "true" : "false";
      }
      var imageField = row.querySelector('[data-field="image"]');
      if (imageField) imageField.value = "";
      var removeImageField = row.querySelector('[data-field="remove_image"]');
      if (removeImageField) removeImageField.checked = false;

      var stockCellNow = row.querySelector("[data-stock-cell]");
      if (stockCellNow && !row.classList.contains("catalog-new-product-row")) {
        var oldBadge = stockCellNow.querySelector(".catalog-stock-pill");
        if (oldBadge) {
          var freshBadge = makeStockBadge(data.quantity || row.getAttribute("data-quantity"), selectedValue(row, "stock_alert_threshold"));
          stockCellNow.replaceChild(freshBadge, oldBadge);
        }
      }

      rowFeedback(row, "Enregistré", false);
      refreshStats();
      applyFilters();
      showNotice(payload.message || "Produit enregistré sans rechargement.", false);
    }).catch(function (error) {
      rowFeedback(row, error.message, true);
      showNotice(error.message, true);
    }).then(function () {
      setBusy(button, false);
    });
  }

  function deleteProduct(row, button) {
    var name = selectedValue(row, "name") || "ce produit";
    var confirmed = window.confirm(
      "Confirmer la suppression de « " + name + " » ?\n\n" +
      "Si ce produit possède du stock ou un historique, il sera archivé au lieu d'être effacé."
    );
    if (!confirmed) return;

    var form = new FormData();
    form.append("product_id", row.getAttribute("data-product-id") || "");
    hideNotice();
    setBusy(button, true, "Suppression…");

    request(productDeleteUrl, form).then(function (payload) {
      var data = payload.data || {};
      if (data.deleted) {
        if (row.parentNode) row.parentNode.removeChild(row);
        if (!tbody.querySelector(".catalog-product-row")) {
          var empty = document.getElementById("catalogEmpty");
          if (!empty) {
            empty = document.createElement("div");
            empty.id = "catalogEmpty";
            empty.className = "catalog-empty";
            empty.textContent = "Aucun produit pour le moment. Ajoutez votre premier produit.";
            var tableResponsive = tbody.closest(".table-responsive");
            if (tableResponsive) tableResponsive.appendChild(empty);
          }
        }
      } else if (data.archived) {
        row.setAttribute("data-active", "false");
        var activeField = row.querySelector('[data-field="is_active"]');
        if (activeField) activeField.value = "false";
        rowFeedback(row, "Archivé — historique conservé", false);
      }

      refreshStats();
      applyFilters();
      showNotice(payload.message || "Opération terminée.", false);
    }).catch(function (error) {
      rowFeedback(row, error.message, true);
      showNotice(error.message, true);
    }).then(function () {
      setBusy(button, false);
    });
  }

  function buildCategoryRow(category) {
    var row = document.createElement("div");
    row.className = "catalog-category-row";
    row.setAttribute("data-category-id", String(category.id));
    row.setAttribute("data-active", category.is_active ? "true" : "false");

    var details = document.createElement("div");
    details.className = "flex-grow-1 min-w-0";
    var input = document.createElement("input");
    input.className = "form-control form-control-sm";
    input.setAttribute("data-category-name", "");
    input.setAttribute("maxlength", "100");
    input.value = category.name;
    input.setAttribute("aria-label", "Nom de la catégorie");
    details.appendChild(input);

    var status = document.createElement("small");
    status.className = "catalog-category-status";
    status.setAttribute("data-category-status", "");
    status.textContent = category.is_active ? "Active" : "Désactivée";
    details.appendChild(status);

    var actions = document.createElement("div");
    actions.className = "catalog-category-actions";
    var save = document.createElement("button");
    save.type = "button";
    save.className = "btn btn-sm btn-outline-primary";
    save.setAttribute("data-action", "save-category");
    save.textContent = "Enregistrer";
    actions.appendChild(save);

    var toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "btn btn-sm btn-light border";
    toggle.setAttribute("data-action", "toggle-category");
    toggle.textContent = category.is_active ? "Désactiver" : "Activer";
    actions.appendChild(toggle);

    row.appendChild(details);
    row.appendChild(actions);
    return row;
  }

  function updateCategoryOptions(category) {
    var id = String(category.id);
    var selects = root.querySelectorAll('select[data-field="category_id"]');
    Array.prototype.forEach.call(selects, function (select) {
      var option = null;
      for (var i = 0; i < select.options.length; i += 1) {
        if (select.options[i].value === id) option = select.options[i];
      }
      var allowInactive = select.getAttribute("data-allow-inactive") === "true";
      if (!category.is_active && !allowInactive) {
        if (option && option.parentNode) option.parentNode.removeChild(option);
        return;
      }
      if (!option) {
        option = document.createElement("option");
        option.value = id;
        select.appendChild(option);
      }
      option.textContent = category.name;
      option.disabled = !category.is_active && allowInactive;
    });

    if (categoryFilter) {
      var filterOption = null;
      for (var j = 0; j < categoryFilter.options.length; j += 1) {
        if (categoryFilter.options[j].value === id) filterOption = categoryFilter.options[j];
      }
      if (!filterOption) {
        filterOption = document.createElement("option");
        filterOption.value = id;
        categoryFilter.appendChild(filterOption);
      }
      filterOption.textContent = category.name;
    }

    if (productTemplate && productTemplate.content) {
      var templateSelect = productTemplate.content.querySelector('select[data-field="category_id"]');
      if (templateSelect) {
        var templateOption = null;
        for (var k = 0; k < templateSelect.options.length; k += 1) {
          if (templateSelect.options[k].value === id) templateOption = templateSelect.options[k];
        }
        if (!category.is_active) {
          if (templateOption && templateOption.parentNode) templateOption.parentNode.removeChild(templateOption);
        } else {
          if (!templateOption) {
            templateOption = document.createElement("option");
            templateOption.value = id;
            templateSelect.appendChild(templateOption);
          }
          templateOption.textContent = category.name;
        }
      }
    }
  }

  function updateCategoryRow(row, category) {
    row.setAttribute("data-active", category.is_active ? "true" : "false");
    var input = row.querySelector("[data-category-name]");
    if (input) input.value = category.name;
    var status = row.querySelector("[data-category-status]");
    if (status) status.textContent = category.is_active ? "Active" : "Désactivée";
    var toggle = row.querySelector('[data-action="toggle-category"]');
    if (toggle) toggle.textContent = category.is_active ? "Désactiver" : "Activer";
    updateCategoryOptions(category);
  }

  function categoryRequest(row, button, values) {
    var form = new FormData();
    form.append("category_id", row.getAttribute("data-category-id"));
    Object.keys(values).forEach(function (key) {
      form.append(key, values[key]);
    });
    setBusy(button, true, "…");
    request(categoriesUrl, form).then(function (payload) {
      updateCategoryRow(row, payload.data);
      refreshStats();
      showNotice(payload.message || "Catégorie mise à jour.", false);
    }).catch(function (error) {
      showNotice(error.message, true);
    }).then(function () {
      setBusy(button, false);
      if (button.getAttribute("data-action") === "toggle-category") {
        var toggle = row.querySelector('[data-action="toggle-category"]');
        if (toggle) toggle.textContent = row.getAttribute("data-active") === "true" ? "Désactiver" : "Activer";
      }
    });
  }

  if (searchInput) searchInput.addEventListener("input", applyFilters);
  if (categoryFilter) categoryFilter.addEventListener("change", applyFilters);
  if (activeFilter) activeFilter.addEventListener("change", applyFilters);

  var clearButton = document.getElementById("catalogClearFilters");
  if (clearButton) clearButton.addEventListener("click", clearFilters);

  var addButton = document.getElementById("addCatalogProduct");
  if (addButton) {
    addButton.addEventListener("click", function () {
      clearFilters();
      var rows = tbody.querySelectorAll(".catalog-product-row");
      var last = rows.length ? rows[rows.length - 1] : null;
      addNewProductRow(last, "");
    });
  }

  root.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("button[data-action]") : null;
    if (!button || !root.contains(button)) return;
    var action = button.getAttribute("data-action");
    var row = button.closest("tr, .catalog-category-row");

    if (action === "insert-after") {
      if (hasActiveFilters()) {
        showNotice("Réinitialisez les filtres pour choisir précisément l'emplacement du nouveau produit.", true);
        return;
      }
      var productRows = Array.prototype.slice.call(tbody.querySelectorAll(".catalog-product-row"));
      var index = productRows.indexOf(row);
      var next = index >= 0 ? productRows[index + 1] : null;
      addNewProductRow(row, next ? next.getAttribute("data-product-id") : "");
      return;
    }

    if (action === "create-product" || action === "save-product") {
      saveProduct(row, button);
      return;
    }

    if (action === "delete-product") {
      deleteProduct(row, button);
      return;
    }

    if (action === "cancel-new") {
      if (row && row.parentNode) row.parentNode.removeChild(row);
      return;
    }

    if (action === "save-category") {
      categoryRequest(row, button, { name: row.querySelector("[data-category-name]").value });
      return;
    }

    if (action === "toggle-category") {
      var nextActive = row.getAttribute("data-active") !== "true";
      categoryRequest(row, button, { is_active: nextActive ? "true" : "false" });
    }
  });

  var categoryForm = document.getElementById("categoryCreateForm");
  if (categoryForm) {
    categoryForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var input = categoryForm.querySelector('[name="name"]');
      if (!input || !input.value.trim()) return;
      var button = categoryForm.querySelector('button[type="submit"]');
      var form = new FormData();
      form.append("name", input.value.trim());
      setBusy(button, true, "…");
      request(categoriesUrl, form).then(function (payload) {
        var category = payload.data;
        var row = buildCategoryRow(category);
        if (categoryList) {
          var empty = categoryList.querySelector(".text-secondary");
          if (empty && empty.parentNode) empty.parentNode.removeChild(empty);
          categoryList.appendChild(row);
        }
        updateCategoryOptions(category);
        input.value = "";
        refreshStats();
        showNotice(payload.message || "Catégorie créée.", false);
      }).catch(function (error) {
        showNotice(error.message, true);
      }).then(function () {
        setBusy(button, false);
      });
    });
  }

  refreshStats();
  applyFilters();
})();