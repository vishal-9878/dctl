cask "dctl" do
  arch arm: "arm64", intel: "x86_64"

  version "0.5.0"
  sha256 arm:   "9e20f537480044792b667d70674252c3586e56de546bb7fd746e302a3dd58786",
         intel: "a5bf555807d8adac61f6211b471f2dc4d660eae2c8fe06fc985fca5c1b82fa39"

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
