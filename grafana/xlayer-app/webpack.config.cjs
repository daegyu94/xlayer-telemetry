const path = require("path");
const CopyPlugin = require("copy-webpack-plugin");
module.exports = {
  entry: "./src/module.tsx",
  output: {
    path: path.resolve(__dirname, "dist"),
    filename: "module.js",
    library: { type: "system" },
    publicPath: "/public/plugins/xlayer-telemetry-app/",
    clean: true,
  },
  resolve: { extensions: [".tsx", ".ts", ".js"] },
  module: {
    rules: [
      {
        test: /\.tsx?$/,
        exclude: /node_modules/,
        use: {
          loader: "ts-loader",
          options: { transpileOnly: true, compilerOptions: { noEmit: false } },
        },
      },
      { test: /\.css$/, use: ["style-loader", "css-loader"] },
    ],
  },
  externals: [
    "react",
    "react-dom",
    "@grafana/data",
    "@grafana/runtime",
    "@grafana/ui",
    "@emotion/css",
    "rxjs",
  ],
  plugins: [
    new CopyPlugin({
      patterns: [
        { from: "src/plugin.json", to: "plugin.json" },
        { from: "src/img", to: "img" },
      ],
    }),
  ],
  optimization: { minimize: true },
  devtool: "source-map",
  performance: { hints: false },
};
