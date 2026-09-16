// jsdom has no layout; actual caret positioning is exercised in browser checks.
if (!Range.prototype.getClientRects) Range.prototype.getClientRects = () => [];
if (!Range.prototype.getBoundingClientRect) Range.prototype.getBoundingClientRect = () => ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 });
