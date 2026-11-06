cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "0.4.7"
  sha256 arm:   "ffa0f39369c2a29fc89bc7f9a40ac6d012b6ccbf294b2decccab75b5b21b2d4d",
         intel: "04fc4f8d97d496a00f73f8aa78301bd380e3cfe82554bc0d3501e0a040135e61"

  url "https://github.com/vishal-9878/dctl/releases/download/v#{version}/dctl-#{version}-macos-#{arch}.pkg"
  name "dctl"
  desc "Agentless desktop app for Docker and PM2 on many servers, over SSH"
  homepage "https://github.com/vishal-9878/dctl"

  livecheck do
    url :url
    strategy :github_latest
  end

  pkg "dctl-#{version}-macos-#{arch}.pkg"

  uninstall pkgutil: "io.github.vishal-9878.dctl",
            delete:  [
              "/Applications/dctl.app",
              "/usr/local/bin/dctl",
              "/usr/local/bin/dctl-ui",
              "/usr/local/lib/dctl",
            ]

  zap trash: [
    "~/.cache/dctl",
    "~/.config/dctl",
  ]
end
