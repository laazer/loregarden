module.exports = {
  Application: class Application {
    async init() {}
    destroy() {}
    stage = { addChild() {} };
    renderer = { resize() {} };
  },
  Assets: { async load() { return {}; } },
  Texture: { WHITE: {} },
  Container: class Container {
    addChild() {}
    removeChildren() { return []; }
    destroy() {}
  },
  Graphics: class Graphics {
    ellipse() { return this; }
    circle() { return this; }
    rect() { return this; }
    fill() { return this; }
    clear() { return this; }
  },
  Sprite: class Sprite {
    anchor = { set() {} };
    destroy() {}
  },
  Text: class Text {
    anchor = { set() {} };
  },
  TextStyle: class TextStyle {},
};
