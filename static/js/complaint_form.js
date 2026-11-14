// Opens and closes the complaint form fields that depend on an earlier choice
// (proposal section 5.1). The server checks the same rules, so the form still works
// without this script; it only saves the lodger from looking at fields that do not apply.
(function () {
  const form = document.getElementById("complaint-form");
  const config = document.getElementById("form-config");
  if (!form || !config) return;
  const { transport, allTransport, local, typeDetails } = JSON.parse(config.textContent);

  const control = (name) => form.elements[name];
  const setVisible = (name, visible) => {
    const wrapper = form.querySelector('[data-field="' + name + '"]');
    if (wrapper) wrapper.hidden = !visible;
  };

  function update() {
    // e. Packing type: Pack opens the pack size (a list, or text before the list is filled in)
    const isPack = control("packing_type").value === "pack";
    setVisible("pack_size_option", isPack);
    setVisible("pack_size", isPack);

    // g. Transport type opens the vessel, vehicle, container and date fields it needs
    const transportType = control("transport_type").value;
    const opened = transport[transportType] || [];
    allTransport.forEach((name) => setVisible(name, opened.includes(name)));

    // The ports are called the place of loading and the destination for a local delivery
    const isLocal = local.includes(transportType);
    form.querySelectorAll("label[data-label-local]").forEach((label) => {
      const text = isLocal ? label.dataset.labelLocal : label.dataset.labelDefault;
      if (label.firstChild && label.firstChild.nodeType === Node.TEXT_NODE) label.firstChild.nodeValue = text;
    });

    // j. Complaint type filters the sub-types; Others opens a short-details field
    const typeId = control("complaint_type").value;
    const subType = control("complaint_sub_type");
    let available = 0;
    Array.from(subType.options).forEach((option) => {
      if (!option.value) return;
      const applies = option.dataset.type === typeId;
      option.hidden = !applies;
      option.disabled = !applies;
      if (applies) available += 1;
    });
    const chosen = subType.selectedOptions[0];
    if (chosen && chosen.disabled) subType.value = "";
    setVisible("complaint_sub_type", available > 0);

    const subTypeNeedsDetails = subType.selectedOptions[0] && subType.selectedOptions[0].dataset.details === "1";
    setVisible("type_details", Boolean(typeDetails[typeId]) || Boolean(subTypeNeedsDetails));
  }

  ["packing_type", "transport_type", "complaint_type", "complaint_sub_type"].forEach((name) =>
    control(name).addEventListener("change", update)
  );
  update();
})();
